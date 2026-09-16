const { Card, Button, IconButton, DataTable, StatusPill, Select, Tag, EmptyState, Toast } = window.PortalDesignSystem_986bf0;

/* Inventory list: coarse filter toolbar, active-filter chips, selectable table, pagination.
   Search, column filters and sorting are the table's own — the toolbar above it only holds
   the two filters that are product concepts rather than columns. */
function InventoryListScreen({ onOpenRecord }) {
  const D = window.NovaData;
  const [region, setRegion] = React.useState("");
  const [status, setStatus] = React.useState("");
  const [selected, setSelected] = React.useState([]);
  const [pageSize] = React.useState(25);
  const [toast, setToast] = React.useState(null);

  const rows = React.useMemo(
    () => D.circuits.filter((c) => (!region || c.region === region) && (!status || c.status === status)),
    [region, status]
  );

  const chips = [
    region && { key: "region", label: "Region: " + region, clear: () => setRegion("") },
    status && { key: "status", label: "Status: " + status, clear: () => setStatus("") },
  ].filter(Boolean);

  return (
    <React.Fragment>
      <Card padding="md" style={{ padding: 0 }}>
        <div style={{ display: "flex", alignItems: "flex-end", gap: "var(--gap-inline)", flexWrap: "wrap" }}>
          <Select size="sm" placeholder="All regions" value={region} onChange={(e) => setRegion(e.target.value)} options={["Midwest", "Northeast", "South", "West"]} style={{ width: 150 }} />
          <Select size="sm" placeholder="Any compliance state" value={status} onChange={(e) => setStatus(e.target.value)} options={[
            { value: "compliant", label: "Compliant" }, { value: "due soon", label: "Due soon" },
            { value: "overdue", label: "Overdue" }, { value: "exception", label: "Exception" },
            { value: "decommissioned", label: "Decommissioned" },
          ]} style={{ width: 190 }} />
          <div style={{ flex: 1 }} />
          <Button size="sm" iconLeft="download">Export CSV</Button>
          <IconButton icon="refresh-cw" label="Refresh" variant="secondary" size="sm" />
        </div>
        {chips.length ? (
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap", marginTop: "var(--space-3)", paddingTop: "var(--space-3)", borderTop: "var(--border-width) solid var(--border-subtle)" }}>
            {chips.map((c) => <Tag key={c.key} onRemove={c.clear}>{c.label}</Tag>)}
            <Button variant="link" size="sm" onClick={() => { setRegion(""); setStatus(""); }}>Clear all</Button>
          </div>
        ) : null}
      </Card>

      {selected.length ? (
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)", padding: "var(--space-2) var(--space-3)", background: "var(--surface-selected)", border: "var(--border-width) solid var(--navy-200)", borderRadius: "var(--radius-md)" }}>
          <span style={{ fontSize: "var(--text-sm)", fontWeight: "var(--weight-medium)" }}>{selected.length} selected</span>
          <Button size="sm" iconLeft="user-plus" onClick={() => setToast("Assigned " + selected.length + " circuits to D. Okafor.")}>Assign owner</Button>
          <Button size="sm" iconLeft="shield-check" onClick={() => setToast("Compliance re-check queued.")}>Re-check compliance</Button>
          <Button size="sm" variant="ghost" onClick={() => setSelected([])}>Clear</Button>
        </div>
      ) : null}

      <Card title="Circuits & WAN links" subtitle={rows.length.toLocaleString() + " of 1,284 records"} padding="none">
        <DataTable
          search
          searchPlaceholder="Circuit ID, carrier, site, IP"
          filterable
          paginated
          pageSize={pageSize}
          defaultSortKey="id"
          selectable
          selected={selected}
          onSelectedChange={setSelected}
          onRowClick={(r) => onOpenRecord(r)}
          columns={[
            { key: "id", header: "Circuit ID", mono: true, width: 130 },
            { key: "carrier", header: "Carrier", width: 110, filter: "select" },
            { key: "site", header: "Site", mono: true, width: 90, filter: "select" },
            { key: "region", header: "Region", width: 110, muted: true, filter: "select" },
            { key: "bandwidth", header: "Bandwidth", align: "right", mono: true, width: 120,
              sortAccessor: (r) => parseFloat(r.bandwidth.replace(/[^\d.]/g, "")) },
            { key: "ip", header: "Handoff IP", mono: true, width: 120 },
            { key: "term", header: "Term ends", mono: true, width: 110, muted: true },
            { key: "monthly", header: "Monthly", align: "right", mono: true, width: 100,
              sortAccessor: (r) => parseFloat(r.monthly.replace(/[^\d.]/g, "")) },
            { key: "status", header: "Compliance", width: 160, filter: "select", render: (r) => <StatusPill status={r.status} /> },
          ]}
          rows={rows}
          emptyState={<EmptyState compact icon="cable" title="No circuits match these filters" description="Clear a filter or widen the search to see records again." action={<Button size="sm" onClick={() => { setRegion(""); setStatus(""); }}>Clear filters</Button>} />}
        />
      </Card>

      {toast ? (
        <div style={{ position: "fixed", right: "var(--space-6)", bottom: "var(--space-6)", zIndex: 1100 }}>
          <Toast tone="success" title="Done" onDismiss={() => setToast(null)}>{toast}</Toast>
        </div>
      ) : null}
    </React.Fragment>
  );
}

Object.assign(window, { InventoryListScreen });
