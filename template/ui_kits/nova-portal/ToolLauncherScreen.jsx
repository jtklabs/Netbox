const { Card, Button, Icon, Input, Badge, Alert, Tag } = window.PortalDesignSystem_986bf0;

/* Tool launcher: the portal is also the front door to everything else the team uses. */
function ToolLauncherScreen() {
  const [query, setQuery] = React.useState("");
  const tools = window.NovaData.tools.filter((t) => (t.name + t.description + t.group).toLowerCase().includes(query.trim().toLowerCase()));
  const groups = ["Monitoring", "Source of truth", "Change", "Access", "Planning"].filter((g) => tools.some((t) => t.group === g));

  return (
    <React.Fragment>
      <Alert tone="info" title="Single sign-on is active">Links open in a new tab using your portal session. The credential vault asks for a second factor.</Alert>

      <div style={{ width: 300 }}>
        <Input size="sm" iconLeft="search" placeholder="Search tools" value={query} onChange={(e) => setQuery(e.target.value)} />
      </div>

      {groups.map((g) => (
        <div key={g} style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
            <h2 style={{ fontSize: "var(--type-section-size)", fontWeight: "var(--type-section-weight)" }}>{g}</h2>
            <Badge tone="neutral">{tools.filter((t) => t.group === g).length}</Badge>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: "var(--space-4)" }}>
            {tools.filter((t) => t.group === g).map((t) => <ToolCard key={t.id} tool={t} />)}
          </div>
        </div>
      ))}

      <Card title="Request a link" subtitle="Tools are added by the portal owners">
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-4)", flexWrap: "wrap" }}>
          <p style={{ margin: 0, flex: 1, minWidth: 280, fontSize: "var(--text-sm)", color: "var(--text-muted)", textWrap: "pretty" }}>
            Missing something your team uses daily? Raise a request and include the URL, the owning team and whether it supports single sign-on.
          </p>
          <Button iconLeft="plus">Request a tool</Button>
        </div>
      </Card>
    </React.Fragment>
  );
}

function ToolCard({ tool }) {
  const [hover, setHover] = React.useState(false);
  return (
    <a
      href="#"
      onClick={(e) => e.preventDefault()}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "flex", gap: "var(--space-3)", alignItems: "flex-start",
        padding: "var(--pad-card)", textDecoration: "none", border: "var(--border-width) solid " + (hover ? "var(--border-strong)" : "var(--border-default)"),
        borderRadius: "var(--radius-card)", background: "var(--surface-card)",
        transition: "var(--transition-control)", minWidth: 0,
      }}
    >
      <span
        style={{
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          width: 34, height: 34, flex: "0 0 auto",
          background: "var(--navy-50)", color: "var(--navy-600)",
          border: "var(--border-width) solid var(--navy-100)", borderRadius: "var(--radius-md)",
        }}
      >
        <Icon name={tool.icon} size={17} />
      </span>
      <span style={{ minWidth: 0, flex: 1 }}>
        <span style={{ display: "flex", alignItems: "center", gap: "var(--space-15)" }}>
          <span style={{ fontSize: "var(--text-base)", fontWeight: "var(--weight-semibold)", color: "var(--text-heading)" }}>{tool.name}</span>
          <Icon name="external-link" size={12} color="var(--text-subtle)" />
        </span>
        <span style={{ display: "block", marginTop: 3, fontSize: "var(--text-sm)", color: "var(--text-muted)", textWrap: "pretty" }}>{tool.description}</span>
      </span>
    </a>
  );
}

Object.assign(window, { ToolLauncherScreen });
