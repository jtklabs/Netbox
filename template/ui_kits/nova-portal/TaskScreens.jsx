const { Card, Button, IconButton, DataTable, StatusPill, Badge, Select, Input, Dialog, FormField, Textarea, Toast, EmptyState, Icon, Tabs } = window.PortalDesignSystem_986bf0;

function TaskListScreen({ onOpenTask }) {
  const D = window.NovaData;
  const [scope, setScope] = React.useState("mine");
  const [selected, setSelected] = React.useState([]);
  const [reassign, setReassign] = React.useState(false);
  const [toast, setToast] = React.useState(null);

  const rows = scope === "mine" ? D.tasks.filter((t) => t.assignee === "D. Okafor") : scope === "overdue" ? D.tasks.filter((t) => t.status === "overdue") : D.tasks;

  return (
    <React.Fragment>
      <Card>
        <div style={{ display: "flex", alignItems: "flex-end", gap: "var(--gap-inline)", flexWrap: "wrap" }}>
          <div style={{ width: 240 }}>
            <Input size="sm" iconLeft="search" placeholder="Task ID or title" />
          </div>
          <Select size="sm" placeholder="Any priority" options={["High", "Medium", "Low"]} style={{ width: 140 }} />
          <Select size="sm" placeholder="Any assignee" options={["D. Okafor", "M. Ruiz", "A. Bello", "J. Lindqvist", "S. Haddad"]} style={{ width: 160 }} />
          <Select size="sm" placeholder="Any due date" options={["Overdue", "Due this week", "Due this month"]} style={{ width: 150 }} />
          <div style={{ flex: 1 }} />
          <Button size="sm" variant="primary" iconLeft="plus">New task</Button>
        </div>
      </Card>

      {selected.length ? (
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)", padding: "var(--space-2) var(--space-3)", background: "var(--surface-selected)", border: "var(--border-width) solid var(--navy-200)", borderRadius: "var(--radius-md)" }}>
          <span style={{ fontSize: "var(--text-sm)", fontWeight: "var(--weight-medium)" }}>{selected.length} selected</span>
          <Button size="sm" iconLeft="user-plus" onClick={() => setReassign(true)}>Reassign</Button>
          <Button size="sm" iconLeft="calendar" >Change due date</Button>
          <Button size="sm" variant="ghost" onClick={() => setSelected([])}>Clear</Button>
        </div>
      ) : null}

      <Card
        title="Task assignments"
        subtitle={rows.length + " tasks"}
        padding="none"
        actions={
          <div style={{ display: "flex", gap: 4 }}>
            {[["mine", "Assigned to me"], ["team", "My team"], ["overdue", "Overdue"]].map(([k, l]) => (
              <Button key={k} size="sm" variant={scope === k ? "secondary" : "ghost"} onClick={() => setScope(k)}>{l}</Button>
            ))}
          </div>
        }
      >
        <DataTable
          selectable
          selected={selected}
          onSelectedChange={setSelected}
          onRowClick={(t) => onOpenTask(t)}
          columns={[
            { key: "id", header: "Task", mono: true, width: 110 },
            { key: "title", header: "Title", wrap: true },
            { key: "related", header: "Related record", mono: true, width: 150, muted: true },
            { key: "assignee", header: "Assignee", width: 130 },
            { key: "priority", header: "Priority", width: 110, render: (t) => <Badge tone={t.priority === "High" ? "danger" : t.priority === "Medium" ? "warning" : "neutral"}>{t.priority}</Badge> },
            { key: "due", header: "Due", mono: true, width: 110 },
            { key: "status", header: "Status", width: 140, render: (t) => <StatusPill status={t.status} /> },
          ]}
          rows={rows}
          emptyState={<EmptyState compact icon="clipboard-check" title="No tasks in this view" description="Nothing is assigned to you right now. New assignments appear here." />}
        />
      </Card>

      <Dialog
        open={reassign}
        onClose={() => setReassign(false)}
        title={"Reassign " + selected.length + " task" + (selected.length === 1 ? "" : "s")}
        description="Assignees are notified by email immediately."
        width={420}
        footer={<React.Fragment><Button onClick={() => setReassign(false)}>Cancel</Button><Button variant="primary" onClick={() => { setReassign(false); setSelected([]); setToast("Now assigned to M. Ruiz."); }}>Reassign</Button></React.Fragment>}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--gap-field)" }}>
          <FormField label="Assign to" htmlFor="at" required>
            <Select id="at" defaultValue="M. Ruiz" options={["M. Ruiz", "A. Bello", "J. Lindqvist", "S. Haddad"]} />
          </FormField>
          <FormField label="Note to assignee" htmlFor="na" optional>
            <Textarea id="na" rows={3} placeholder="Why this is moving." />
          </FormField>
        </div>
      </Dialog>

      {toast ? (
        <div style={{ position: "fixed", right: "var(--space-6)", bottom: "var(--space-6)", zIndex: 1100 }}>
          <Toast tone="success" title="Tasks reassigned" onDismiss={() => setToast(null)} action={<Button variant="link" size="sm" onClick={() => setToast(null)}>Undo</Button>}>{toast}</Toast>
        </div>
      ) : null}
    </React.Fragment>
  );
}

function TaskDetailScreen({ task, onNavigate }) {
  const t = task || window.NovaData.tasks[0];
  const [done, setDone] = React.useState(false);
  return (
    <React.Fragment>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,2fr) minmax(0,1fr)", gap: "var(--space-4)", alignItems: "start" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
          <Card
            title={t.title}
            subtitle={"Opened 12 September 2026 by the compliance scheduler"}
            footer={<React.Fragment><Button onClick={() => onNavigate("tasks")}>Back to tasks</Button><Button variant="secondary" iconLeft="user-plus">Reassign</Button><Button variant="primary" iconLeft="check" onClick={() => setDone(true)}>Mark complete</Button></React.Fragment>}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
              <p style={{ margin: 0, fontSize: "var(--text-base)", textWrap: "pretty", maxWidth: 680 }}>
                Export the current firewall rule set from dal03-fw-01 and attach it to REP-2291. The export must include rule descriptions,
                last-hit timestamps and the change ticket recorded against each rule. Rules with no hit in 180 days need a justification or a removal ticket.
              </p>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0,1fr))", gap: "var(--space-4)" }}>
                {[["Task", t.id], ["Assignee", t.assignee], ["Due", t.due], ["Priority", t.priority]].map(([l, v]) => (
                  <div key={l} style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                    <span style={{ fontSize: "var(--text-2xs)", letterSpacing: "var(--tracking-caps)", textTransform: "uppercase", fontWeight: 600, color: "var(--text-muted)" }}>{l}</span>
                    <span style={{ fontSize: "var(--text-base)", fontFamily: l === "Task" || l === "Due" ? "var(--font-mono)" : "inherit" }}>{v}</span>
                  </div>
                ))}
              </div>
            </div>
          </Card>

          <Card title="Checklist" padding="none">
            <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
              {[["Pull rule export from the firewall", true], ["Reconcile rules against change tickets", true], ["Flag rules with no hits in 180 days", false], ["Attach export to REP-2291", false]].map(([label, complete]) => (
                <li key={label} style={{ display: "flex", alignItems: "center", gap: "var(--space-3)", padding: "var(--space-3) var(--pad-card)", borderBottom: "var(--border-width) solid var(--border-subtle)" }}>
                  <Icon name={complete ? "circle-check" : "circle"} size={15} color={complete ? "var(--success-ink)" : "var(--text-subtle)"} />
                  <span style={{ flex: 1, fontSize: "var(--text-sm)", color: complete ? "var(--text-muted)" : "var(--text-body)", textDecoration: complete ? "line-through" : "none" }}>{label}</span>
                </li>
              ))}
            </ul>
          </Card>

          <Card title="Comments">
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
              {[["M. Ruiz", "13 Sep 09:12", "Export is blocked — the read-only API token expired. Raised CHG-14903 to rotate it."], ["D. Okafor", "13 Sep 10:40", "Token rotated. Try again and let me know if the 403 persists."]].map((c) => (
                <div key={c[1]} style={{ display: "flex", gap: "var(--space-3)" }}>
                  <span style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 28, height: 28, flex: "0 0 auto", background: "var(--navy-50)", color: "var(--navy-600)", borderRadius: "var(--radius-pill)", fontSize: "var(--text-2xs)", fontWeight: 600 }}>
                    {c[0].split(" ").map((s) => s[0]).join("")}
                  </span>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ display: "flex", gap: "var(--space-2)", alignItems: "baseline" }}>
                      <span style={{ fontSize: "var(--text-sm)", fontWeight: "var(--weight-medium)" }}>{c[0]}</span>
                      <span style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>{c[1]}</span>
                    </div>
                    <p style={{ margin: "2px 0 0", fontSize: "var(--text-sm)", textWrap: "pretty" }}>{c[2]}</p>
                  </div>
                </div>
              ))}
              <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)", alignItems: "flex-start" }}>
                <Textarea rows={2} placeholder="Add a comment" />
                <Button size="sm">Comment</Button>
              </div>
            </div>
          </Card>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
          <Card title="Status">
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
              <StatusPill status={done ? "approved" : t.status} />
              <div style={{ fontSize: "var(--text-sm)", color: "var(--text-muted)" }}>Blocked 2 days · reopened once</div>
            </div>
          </Card>
          <Card title="Related records" padding="none">
            <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
              {[["shield-check", "REP-2291", "Quarterly firewall rule review"], ["server", "dal03-fw-01", "Palo Alto PA-3440 · DAL-03"], ["ticket", "CHG-14903", "Rotate read-only API token"]].map((r) => (
                <li key={r[1]} style={{ display: "flex", alignItems: "center", gap: "var(--space-3)", padding: "var(--space-3) var(--pad-card)", borderBottom: "var(--border-width) solid var(--border-subtle)" }}>
                  <Icon name={r[0]} size={15} color="var(--text-subtle)" />
                  <span style={{ minWidth: 0 }}>
                    <span style={{ display: "block", fontFamily: "var(--font-mono)", fontSize: "var(--text-sm)", color: "var(--text-link)" }}>{r[1]}</span>
                    <span style={{ display: "block", fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>{r[2]}</span>
                  </span>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>
    </React.Fragment>
  );
}

Object.assign(window, { TaskListScreen, TaskDetailScreen });
