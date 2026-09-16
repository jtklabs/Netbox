const { Card, Button, MetricTile, DataTable, StatusPill, ProgressMeter, Alert, Icon, Badge } = window.PortalDesignSystem_986bf0;

function DashboardScreen({ onNavigate }) {
  const D = window.NovaData;
  return (
    <React.Fragment>
      <Alert
        tone="danger"
        title="REP-2291 is past due"
        action={<Button variant="link" size="sm" onClick={() => onNavigate("reports")}>Open the report</Button>}
      >
        The quarterly firewall rule review was due 30 September. Evidence is 74% collected.
      </Alert>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0,1fr))", gap: "var(--space-4)" }}>
        <MetricTile label="Assets under management" value="4,182" icon="server" delta="+38 this month" deltaTone="up" onClick={() => onNavigate("devices")} />
        <MetricTile label="Overdue reports" value="3" icon="shield-alert" delta="+1 vs last week" deltaTone="down" onClick={() => onNavigate("reports")} />
        <MetricTile label="Open tasks" value="12" icon="clipboard-list" footnote="4 due this week" onClick={() => onNavigate("tasks")} />
        <MetricTile label="Config drift" value="2.4" unit="%" icon="git-compare" delta="-0.6 pts" deltaTone="up" />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,2fr) minmax(0,1fr)", gap: "var(--space-4)", alignItems: "start" }}>
        <Card
          title="Compliance reporting"
          subtitle="Current period"
          padding="none"
          actions={<Button size="sm" onClick={() => onNavigate("reports")}>View all</Button>}
        >
          <DataTable
            columns={[
              { key: "id", header: "Report", mono: true, width: 100 },
              { key: "name", header: "Name", wrap: true },
              { key: "framework", header: "Framework", width: 120, muted: true },
              { key: "due", header: "Due", mono: true, width: 110 },
              { key: "coverage", header: "Evidence", width: 140, render: (r) => <ProgressMeter size="sm" value={r.coverage} valueText={r.coverage + "%"} tone={r.coverage === 100 ? "success" : r.coverage < 80 ? "danger" : "warning"} /> },
              { key: "status", header: "Status", width: 140, render: (r) => <StatusPill status={r.status} /> },
            ]}
            rows={D.reports.slice(0, 5)}
            onRowClick={() => onNavigate("reports")}
          />
        </Card>

        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
          <Card title="My tasks" subtitle="Assigned to you" padding="none" actions={<Button size="sm" variant="ghost" onClick={() => onNavigate("tasks")}>All tasks</Button>}>
            <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
              {D.tasks.slice(0, 4).map((t) => (
                <li
                  key={t.id}
                  style={{
                    display: "flex", flexDirection: "column", gap: 4,
                    padding: "var(--space-3) var(--pad-card)",
                    borderBottom: "var(--border-width) solid var(--border-subtle)",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>{t.id}</span>
                    <StatusPill status={t.status} />
                  </div>
                  <div style={{ fontSize: "var(--text-sm)", textWrap: "pretty" }}>{t.title}</div>
                  <div style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>Due {t.due} · {t.assignee}</div>
                </li>
              ))}
            </ul>
          </Card>

          <Card title="Recent activity">
            <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
              {D.activity.map((a, i) => (
                <li key={i} style={{ display: "flex", gap: "var(--space-3)", alignItems: "flex-start" }}>
                  <span style={{ color: "var(--text-subtle)", marginTop: 2 }}><Icon name={a.icon} size={14} /></span>
                  <span style={{ flex: 1, minWidth: 0, fontSize: "var(--text-sm)", textWrap: "pretty" }}>
                    <strong style={{ fontWeight: "var(--weight-medium)" }}>{a.who}</strong> {a.what}
                    <span style={{ display: "block", fontSize: "var(--text-xs)", color: "var(--text-muted)", marginTop: 1 }}>{a.when}</span>
                  </span>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>

      <Card
        title="Inventory by region"
        subtitle="Circuits with an active contract"
        actions={<Badge tone="neutral">Updated 15 min ago</Badge>}
      >
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0,1fr))", gap: "var(--space-6)" }}>
          {[["Midwest", 1284, 1400], ["Northeast", 962, 1400], ["South", 1118, 1400], ["West", 818, 1400]].map(([r, v, m]) => (
            <div key={r} style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
              <div style={{ display: "flex", alignItems: "baseline", gap: "var(--space-2)" }}>
                <span style={{ fontSize: "var(--text-xl)", fontWeight: 600, fontVariantNumeric: "tabular-nums", color: "var(--text-heading)" }}>{v.toLocaleString()}</span>
                <span style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>{r}</span>
              </div>
              <ProgressMeter value={v} max={m} size="sm" />
            </div>
          ))}
        </div>
      </Card>
    </React.Fragment>
  );
}

Object.assign(window, { DashboardScreen });
