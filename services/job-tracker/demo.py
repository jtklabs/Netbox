"""Explicitly simulated data for local preview; never used in live mode."""
import time
from datetime import datetime, timedelta, timezone


def sample():
    current = datetime.now(timezone.utc)
    scheduled = current.replace(minute=0, second=0, microsecond=0)
    batch = 'df82e7c0-45f6-4ad1-82ed-96a03172ab21'
    devices, jobs = {}, []
    specs = [
        ('nyc-access-01','access','Access','NYC · East closets',1,'completed','complete','Upgrade verified. Running the approved release.'),
        ('nyc-access-02','access','Access','NYC · West closets',1,'completed','complete','Postchecks passed. Configuration saved.'),
        ('nyc-access-03','access','Access','NYC · East closets',2,'running','reconnecting','Waiting for the device to return after reload.'),
        ('nyc-access-04','access','Access','NYC · West closets',2,'claimed','precheck','Collecting baseline and checking image integrity.'),
        ('nyc-dist-01','distribution','Distribution','NYC · Distribution pair',3,'held','held',''),
        ('nyc-dist-02','distribution','Distribution','NYC · Distribution pair',4,'pending','',''),
        ('nyc-core-01','core','Core','NYC · Core pair',5,'pending','',''),
        ('nyc-core-02','core','Core','NYC · Core pair',6,'pending','',''),
    ]
    for pk, (name,slug,role,group,wave,status,stage,message) in enumerate(specs,1):
        devices[pk] = {'id':pk,'role':{'name':role,'slug':slug},'site':{'name':'New York'}}
        if pk == 4 and int(time.time() / 15) % 2:
            stage, message = 'precheck_complete', 'Baseline collected. Checking available flash space.'
        seen = (current - timedelta(seconds=4 + pk)).isoformat()
        jobs.append({'id':4100+pk,'batch_id':batch,'device':{'id':pk},'device_name':name,'poller':{'name':'nyc-01'},
                     'status':{'value':status},'operation':{'value':'upgrade'},'scheduled_at':scheduled.isoformat(),
                     'start_before':(scheduled + timedelta(hours=4)).isoformat(), 'started_at':scheduled.isoformat() if pk < 4 else None,
                     'completed_at':seen if status == 'completed' else None,'last_seen_at':seen if pk <= 4 else None,
                     'poller_last_seen_at':seen,'heartbeat_stale':False,'needs_recovery':False,
                     'planned_wave':wave,'groups':[group],'waits_for':[3,4] if pk in (5,6) else [5,6] if pk in (7,8) else [],
                     'stage':stage,'message':message,'held_reason':'Operator hold: confirm downstream health before continuing.' if pk == 5 else '',
                     'description':'New York · Campus software upgrade',
                     'events':[{'timestamp':seen,'stage':stage,'message':message}] if stage else []})
    groups = [{'name':group,'max_concurrent':1,'stale':False} for group in dict.fromkeys(row[3] for row in specs)]
    # Exercise the distinct job types without assigning artificial upgrade groups.
    for offset, (operation,status,stage,message) in enumerate([
        ('stage','running','copying','Copying and verifying the approved software image.'),
        ('audit_config','completed','complete','Configuration standards audit passed.'),
        ('remediate','pending','','Waiting to apply the selected configuration standards.'),
    ], 1):
        jobs.append({**jobs[offset - 1], 'id':4200+offset,
                     'batch_id':'ef82e7c0-45f6-4ad1-82ed-96a03172ab21',
                     'operation':{'value':operation},'status':{'value':status},
                     'stage':stage,'message':message,'groups':[],'waits_for':[],
                     'held_reason':'','planned_wave':1,'description':'Software staging and standards',
                     'started_at':scheduled.isoformat() if status != 'pending' else None,
                     'completed_at':seen if status == 'completed' else None,
                     'last_seen_at':seen if status != 'pending' else None,
                     'events':[{'timestamp':seen,'stage':stage,'message':message}] if stage else []})
    return jobs, devices, groups
