const { Card, Button, IconButton, DataTable, StatusPill, Badge, Icon, Alert, Dialog, FormField, Select, Textarea, Tag, ProgressMeter } = window.PortalDesignSystem_986bf0;

function Field({ label, value, mono }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
      <span style={{ fontSize: "var(--text-2xs)", letterSpacing: "var(--tracking-caps)", textTransform: "uppercase", fontWeight: 600, color: "var(--text-muted)" }}>{label}</span>
      <span style={{ fontSize: "var(--text-base)", fontFamily: mono ? "var(--font-mono)" : "inherit", fontVariantNumeric: mono ? "tabular-nums" : undefined, overflow: "hidden", textOverflow: "ellipsis" }}>{value}</span>
    </div>
  );
}

function InventoryDetailScreen({ record, tab }) {
  const r = record || window.NovaData.circuits[0];
  const [dialog, setDialog] = React.useState(false);

  if (tab === "compliance") {
    return (
      <React.Fragment>
        <Alert tone="warning" title="One control is in exception until 31 Oct 2026">Exception CE-118 approved by the network risk board on 4 Aug 2026.</Alert>
        <Card title="Control conformance" subtitle="Internal standard CS-11 · evaluated nightly" padding="none">
          <DataTable
            columns={[
              { key: "id", header: "Control", mono: true, width: 110 },
              { key: "name", header: "Requirement", wrap: true },
              { key: "checked", header: "Last checked", mono: true, width: 140, muted: true },
              { key: "status", header: "Result", width: 150, render: (x) => <StatusPill status={x.status} /> },
            ]}
            rows={[
              { id: "CS-11.1", name: "Management plane reachable only from the jump network", checked: "2026-09-15 02:14", status: "passed" },
              { id: "CS-11.2", name: "AAA configured against both RADIUS clusters", checked: "2026-09-15 02:14", status: "passed" },
              { id: "CS-11.4", name: "NTP peers match the approved source list", checked: "2026-09-15 02:14", status: "exception" },
              { id: "CS-11.7", name: "Interface descriptions carry the circuit ID", checked: "2026-09-15 02:14", status: "passed" },
              { id: "CS-11.9", name: "Config archive within 24 hours of last change", checked: "2026-09-15 02:14", status: "passed" },
            ]}
          />
        </Card>
      </React.Fragment>
    );
  }

  if (tab === "history") {
    return (
      <Card title="Change history" padding="none">
        <DataTable
          compact
          columns={[
            { key: "when", header: "When", mono: true, width: 150 },
            { key: "who", header: "Who", width: 140 },
            { key: "what", header: "Change", wrap: true },
            { key: "ticket", header: "Ticket", mono: true, width: 120, muted: true },
          ]}
          rows={[
            { id: 1, when: "2026-09-12 09:41", who: "D. Okafor", what: "Bandwidth increased 500 → 1,000 Mbps", ticket: "CHG-14882" },
            { id: 2, when: "2026-08-30 16:02", who: "Discovery job", what: "Handoff IP updated to 10.42.8.1", ticket: "—" },
            { id: 3, when: "2026-06-18 11:20", who: "S. Haddad", what: "Contract term extended to 2027-03-31", ticket: "CHG-14106" },
            { id: 4, when: "2026-02-04 08:55", who: "M. Ruiz", what: "Record created from carrier order LUM-77213", ticket: "CHG-12990" },
          ]}
        />
      </Card>
    );
  }

  return (
    <React.Fragment>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,2fr) minmax(0,1fr)", gap: "var(--space-4)", alignItems: "start" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
          <Card title="Circuit" actions={<Button size="sm" iconLeft="pencil" onClick={() => setDialog(true)}>Edit</Button>}>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0,1fr))", gap: "var(--space-5) var(--space-6)" }}>
              <Field label="Circuit ID" value={r.id} mono />
              <Field label="Carrier" value={r.carrier} />
              <Field label="Carrier order" value="LUM-77213" mono />
              <Field label="Bandwidth" value={r.bandwidth} mono />
              <Field label="Handoff IP" value={r.ip} mono />
              <Field label="Term ends" value={r.term} mono />
              <Field label="Site" value={r.site + " · " + r.region} />
              <Field label="Monthly cost" value={r.monthly} mono />
              <Field label="Record owner" value="D. Okafor" />
            </div>
          </Card>

          <Card title="Terminating devices" padding="none" actions={<Button size="sm" variant="ghost" iconLeft="external-link">Open in NetBox</Button>}>
            <DataTable
              compact
              columns={[
                { key: "id", header: "Hostname", mono: true, width: 150 },
                { key: "model", header: "Model" },
                { key: "role", header: "Role", width: 130, muted: true },
                { key: "ip", header: "Mgmt IP", mono: true, width: 120 },
                { key: "status", header: "Standard", width: 140, render: (x) => <StatusPill status={x.status} /> },
              ]}
              rows={window.NovaData.devices.slice(0, 2)}
            />
          </Card>

          <Card title="Attached documents" padding="none">
            <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
              {[["Lumen MSA — executed.pdf", "2.4 MB", "S. Haddad", "2026-02-04"], ["LOA-CFA CHI-01.pdf", "310 KB", "D. Okafor", "2026-02-11"], ["Bandwidth upgrade approval.pdf", "185 KB", "M. Ruiz", "2026-09-12"]].map((d) => (
                <li key={d[0]} style={{ display: "flex", alignItems: "center", gap: "var(--space-3)", padding: "var(--space-3) var(--pad-card)", borderBottom: "var(--border-width) solid var(--border-subtle)" }}>
                  <Icon name="file-text" size={15} color="var(--text-subtle)" />
                  <span style={{ flex: 1, minWidth: 0, fontSize: "var(--text-sm)" }}>{d[0]}</span>
                  <span style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)", fontVariantNumeric: "tabular-nums" }}>{d[1]}</span>
                  <span style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>{d[2]} · {d[3]}</span>
                  <IconButton icon="download" label="Download" size="sm" />
                </li>
              ))}
            </ul>
          </Card>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
          <Card title="Compliance">
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                <StatusPill status={r.status} />
                <span style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>checked 02:14 today</span>
              </div>
              <ProgressMeter label="Controls passed" value={4} max={5} valueText="4 of 5" tone="warning" />
              <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-1)" }}>
                <Badge tone="brand">CS-11</Badge>
                <Badge tone="neutral">SOX in scope</Badge>
                <Badge tone="warning">Exception CE-118</Badge>
              </div>
            </div>
          </Card>

          <Card title="Open tasks" padding="none">
            <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
              {window.NovaData.tasks.slice(0, 2).map((t) => (
                <li key={t.id} style={{ display: "flex", flexDirection: "column", gap: 3, padding: "var(--space-3) var(--pad-card)", borderBottom: "var(--border-width) solid var(--border-subtle)" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>{t.id}</span>
                    <StatusPill status={t.status} />
                  </div>
                  <span style={{ fontSize: "var(--text-sm)", textWrap: "pretty" }}>{t.title}</span>
                </li>
              ))}
            </ul>
          </Card>

          <Card title="Tags">
            <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)" }}>
              <Tag icon="map-pin">CHI-01</Tag>
              <Tag>Tier 1 site</Tag>
              <Tag>Dual-homed</Tag>
              <Tag>SNMP v3</Tag>
              <Button variant="ghost" size="sm" iconLeft="plus">Add</Button>
            </div>
          </Card>
        </div>
      </div>

      <Dialog
        open={dialog}
        onClose={() => setDialog(false)}
        title={"Edit " + r.id}
        description="Changes are written to the audit log and require a change ticket."
        width={520}
        footer={<React.Fragment><Button onClick={() => setDialog(false)}>Cancel</Button><Button variant="primary" onClick={() => setDialog(false)}>Save changes</Button></React.Fragment>}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--gap-field)" }}>
          <FormField label="Compliance state" htmlFor="cs" required>
            <Select id="cs" defaultValue="compliant" options={[{ value: "compliant", label: "Compliant" }, { value: "exception", label: "Exception" }, { value: "overdue", label: "Overdue" }]} />
          </FormField>
          <FormField label="Change ticket" htmlFor="ct" required hint="An open CHG ticket is required for inventory edits.">
            <Select id="ct" placeholder="Select a ticket" options={["CHG-14882", "CHG-14901", "CHG-14903"]} />
          </FormField>
          <FormField label="Note" htmlFor="nt" optional>
            <Textarea id="nt" rows={3} placeholder="What changed and why." />
          </FormField>
        </div>
      </Dialog>
    </React.Fragment>
  );
}

Object.assign(window, { InventoryDetailScreen });
