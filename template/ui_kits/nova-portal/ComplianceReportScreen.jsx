const { Card, Button, IconButton, DataTable, StatusPill, ProgressMeter, Select, Input, Alert, MetricTile, Badge } = window.PortalDesignSystem_986bf0;

/* Compliance report register + a single report's detail view. */
function ComplianceReportScreen({ onNavigate, onOpenReport, report }) {
  const D = window.NovaData;

  if (report) {
    return (
      <React.Fragment>
        <Alert tone="danger" title="Evidence is incomplete" action={<Button variant="link" size="sm" onClick={() => onNavigate("submit")}>Submit evidence</Button>}>
          6 of 23 firewalls have no rule export attached for this period.
        </Alert>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0,1fr))", gap: "var(--space-4)" }}>
          <MetricTile label="Framework" value={report.framework} icon="shield-check" />
          <MetricTile label="Period" value={report.period} icon="calendar" />
          <MetricTile label="Due" value={report.due} icon="clock" footnote="Past due" />
          <MetricTile label="Evidence" value={report.coverage + "%"} icon="paperclip" footnote="17 of 23 items" />
        </div>

        <Card title="In-scope assets" subtitle="23 firewalls across 4 regions" padding="none" actions={<Button size="sm" iconLeft="download">Export evidence pack</Button>}>
          <DataTable
            columns={[
              { key: "id", header: "Hostname", mono: true, width: 150 },
              { key: "model", header: "Model" },
              { key: "site", header: "Site", mono: true, width: 90 },
              { key: "reviewer", header: "Reviewer", width: 130 },
              { key: "evidence", header: "Evidence", width: 170, render: (r) => <span style={{ fontSize: "var(--text-sm)", color: r.evidence === "Missing" ? "var(--text-danger)" : "var(--text-body)" }}>{r.evidence}</span> },
              { key: "status", header: "Result", width: 150, render: (r) => <StatusPill status={r.status} /> },
            ]}
            rows={[
              { id: "dal03-fw-01", model: "Palo Alto PA-3440", site: "DAL-03", reviewer: "M. Ruiz", evidence: "Missing", status: "failed" },
              { id: "chi01-fw-01", model: "Palo Alto PA-5410", site: "CHI-01", reviewer: "M. Ruiz", evidence: "rules-chi01.csv", status: "passed" },
              { id: "atl02-fw-02", model: "Fortinet FG-600F", site: "ATL-02", reviewer: "A. Bello", evidence: "rules-atl02.csv", status: "passed" },
              { id: "nyc04-fw-01", model: "Palo Alto PA-3440", site: "NYC-04", reviewer: "A. Bello", evidence: "rules-nyc04.csv", status: "exception" },
              { id: "sea02-fw-01", model: "Fortinet FG-400F", site: "SEA-02", reviewer: "D. Okafor", evidence: "Missing", status: "failed" },
            ]}
          />
        </Card>

        <Card title="Submission trail" padding="none">
          <DataTable
            compact
            columns={[
              { key: "when", header: "When", mono: true, width: 150 },
              { key: "who", header: "Who", width: 140 },
              { key: "what", header: "Event", wrap: true },
            ]}
            rows={[
              { id: 1, when: "2026-09-14 11:08", who: "System", what: "Marked overdue — due date passed with incomplete evidence" },
              { id: 2, when: "2026-09-09 15:47", who: "M. Ruiz", what: "Attached rules-chi01.csv and rules-atl02.csv" },
              { id: 3, when: "2026-09-01 08:00", who: "System", what: "Report opened for Q3 2026 and assigned to M. Ruiz" },
            ]}
          />
        </Card>
      </React.Fragment>
    );
  }

  return (
    <React.Fragment>
      <Card>
        <div style={{ display: "flex", alignItems: "flex-end", gap: "var(--gap-inline)", flexWrap: "wrap" }}>
          <div style={{ width: 240 }}>
            <Input size="sm" iconLeft="search" placeholder="Report ID or name" />
          </div>
          <Select size="sm" placeholder="All frameworks" options={["SOX", "PCI DSS 4.0", "Internal CS-11", "Internal CS-04"]} style={{ width: 170 }} />
          <Select size="sm" placeholder="All periods" options={["Q3 2026", "H2 2026", "Sep 2026", "Aug 2026"]} style={{ width: 150 }} />
          <Select size="sm" placeholder="Any status" options={["Overdue", "In review", "Due soon", "Submitted", "Approved"]} style={{ width: 150 }} />
          <div style={{ flex: 1 }} />
          <Button size="sm" variant="primary" iconLeft="file-plus-2" onClick={() => onNavigate("submit")}>New submission</Button>
        </div>
      </Card>

      <Card title="Report register" subtitle="Rolling twelve months" padding="none" actions={<Badge tone="danger">3 overdue</Badge>}>
        <DataTable
          onRowClick={(r) => onOpenReport(r)}
          columns={[
            { key: "id", header: "Report", mono: true, width: 100 },
            { key: "name", header: "Name", wrap: true },
            { key: "framework", header: "Framework", width: 130, muted: true },
            { key: "period", header: "Period", width: 100, muted: true },
            { key: "owner", header: "Owner", width: 120 },
            { key: "due", header: "Due", mono: true, width: 110 },
            { key: "coverage", header: "Evidence", width: 150, render: (r) => <ProgressMeter size="sm" value={r.coverage} valueText={r.coverage + "%"} tone={r.coverage === 100 ? "success" : r.coverage < 80 ? "danger" : "warning"} /> },
            { key: "status", header: "Status", width: 150, render: (r) => <StatusPill status={r.status} /> },
          ]}
          rows={D.reports}
        />
      </Card>

      <Card title="Configuration standards" subtitle="Evaluated nightly against every managed device" padding="none">
        <DataTable
          compact
          columns={[
            { key: "id", header: "Standard", mono: true, width: 120 },
            { key: "name", header: "Name", wrap: true },
            { key: "scope", header: "Scope", width: 180, muted: true },
            { key: "pass", header: "Conformance", width: 170, render: (r) => <ProgressMeter size="sm" value={r.pass} valueText={r.pass + "%"} tone={r.pass >= 98 ? "success" : r.pass >= 90 ? "warning" : "danger"} /> },
          ]}
          rows={[
            { id: "CS-11", name: "Device configuration baseline", scope: "4,182 devices", pass: 96 },
            { id: "CS-04", name: "Backup and restore verification", scope: "4,182 devices", pass: 99 },
            { id: "CS-07", name: "Credential rotation", scope: "312 accounts", pass: 88 },
            { id: "CS-19", name: "Perimeter rule hygiene", scope: "23 firewalls", pass: 74 },
          ]}
        />
      </Card>
    </React.Fragment>
  );
}

Object.assign(window, { ComplianceReportScreen });
