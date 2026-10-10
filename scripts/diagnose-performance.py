"""Read-only production checks; run from the deployment checkout:

docker compose exec -T netbox /opt/netbox/venv/bin/python \
    /opt/netbox/netbox/manage.py shell < scripts/diagnose-performance.py

Uses NetBox's existing connection without printing credentials, device names,
or active SQL text. Each section has a five-second statement timeout. Run while
the slowdown is happening to capture transient database waits.
"""
import json
import statistics
from datetime import datetime, timezone
from time import perf_counter

from django.conf import settings
from django.db import connection, transaction


report = {
    'collected_at': datetime.now(timezone.utc).isoformat(),
    'netbox_version': settings.VERSION,
    'debug': settings.DEBUG,
}


def read_section(name, collect):
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION READ ONLY')
            cursor.execute("SET LOCAL statement_timeout = '5s'")
            report[name] = collect(cursor)
    except Exception as exc:
        # Connection errors may contain infrastructure details: report only
        # the error type, keeping the rest of the diagnostic usable.
        report[name] = {'unavailable': type(exc).__name__}


def round_trips(cursor):
    cursor.execute('SELECT 1')
    cursor.fetchone()
    samples = []
    for _ in range(20):
        started = perf_counter()
        cursor.execute('SELECT 1')
        cursor.fetchone()
        samples.append((perf_counter() - started) * 1000)
    return {
        'samples': len(samples),
        'median_ms': round(statistics.median(samples), 2),
        'max_ms': round(max(samples), 2),
    }


def query(sql):
    def collect(cursor):
        started = perf_counter()
        cursor.execute(sql)
        columns = [column[0] for column in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return {'elapsed_ms': round((perf_counter() - started) * 1000, 2), 'rows': rows}
    return collect


read_section('database_round_trip', round_trips)
read_section('inventory', query('''
    SELECT (SELECT count(*) FROM dcim_device) AS devices,
           (SELECT count(*) FROM dcim_interface) AS interfaces
'''))
read_section('largest_devices_by_interface_count', query('''
    SELECT device_id, count(*) AS interfaces FROM dcim_interface
    GROUP BY device_id ORDER BY interfaces DESC, device_id LIMIT 10
'''))
read_section('table_statistics', query('''
    SELECT relname AS table_name, n_live_tup AS estimated_live_rows,
           n_dead_tup AS estimated_dead_rows, n_mod_since_analyze,
           last_analyze, last_autoanalyze, last_vacuum, last_autovacuum
    FROM pg_stat_user_tables
    WHERE relname IN ('dcim_device', 'dcim_interface', 'core_objectchange',
                      'extras_taggeditem', 'netbox_discovery_upgradejob')
    ORDER BY relname
'''))
read_section('active_sessions_and_waits', query('''
    SELECT pid, state, wait_event_type, wait_event,
           EXTRACT(EPOCH FROM (clock_timestamp() - xact_start))::integer AS transaction_age_seconds,
           cardinality(pg_blocking_pids(pid)) AS blocking_session_count
    FROM pg_stat_activity
    WHERE datname = current_database() AND pid <> pg_backend_pid()
      AND state IS DISTINCT FROM 'idle'
    ORDER BY xact_start NULLS LAST LIMIT 15
'''))
read_section('interface_indexes', query('''
    SELECT idx.relname AS index_name, i.indisvalid AS valid,
           i.indisready AS ready, pg_get_indexdef(i.indexrelid) AS definition
    FROM pg_index i
    JOIN pg_class tbl ON tbl.oid = i.indrelid
    JOIN pg_class idx ON idx.oid = i.indexrelid
    JOIN pg_namespace ns ON ns.oid = tbl.relnamespace
    WHERE tbl.relname = 'dcim_interface' AND ns.nspname = current_schema()
    ORDER BY idx.relname
'''))

print(json.dumps(report, indent=2, default=str))
