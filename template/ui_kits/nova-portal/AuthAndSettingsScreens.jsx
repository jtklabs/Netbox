const { Button, FormField, Input, Checkbox, Alert, Icon, Card, Tabs, Switch, Select, Badge, DataTable, StatusPill, IconButton } = window.PortalDesignSystem_986bf0;

/* Login: split layout. Left is the navy brand panel (name in type — no logo file exists).
   Right is the form. SSO first, credentials second, because most staff use SSO. */
function LoginScreen({ onSignIn }) {
  const [failed, setFailed] = React.useState(false);
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 480px", height: "100%", background: "transparent" }}>
      {/* The brand panel stays solid navy — it is the one place the portal is not frosted,
          because white display type needs an opaque ground. */}
      <div style={{ background: "var(--surface-brand-panel)", padding: "48px", display: "flex", flexDirection: "column", justifyContent: "space-between" }}>
        <span style={{ fontSize: 40, fontWeight: 700, letterSpacing: "-0.03em", color: "#fff" }}>Nova</span>
        <div style={{ maxWidth: 460 }}>
          <h1 style={{ fontSize: 32, fontWeight: 600, letterSpacing: "-0.02em", color: "#fff", lineHeight: 1.2 }}>Network operations portal</h1>
          <p style={{ margin: "12px 0 0", fontSize: "var(--text-md)", color: "rgba(255,255,255,.82)", lineHeight: 1.6, textWrap: "pretty" }}>
            Inventory, compliance reporting, task assignments and links to the rest of the toolchain.
          </p>
          <ul style={{ listStyle: "none", margin: "28px 0 0", padding: 0, display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
            {[["server", "4,182 managed assets"], ["shield-check", "6 reporting frameworks"], ["clock", "Records synced every 15 minutes"]].map((f) => (
              <li key={f[1]} style={{ display: "flex", alignItems: "center", gap: "var(--space-3)", color: "rgba(255,255,255,.78)", fontSize: "var(--text-sm)" }}>
                <Icon name={f[0]} size={15} />{f[1]}
              </li>
            ))}
          </ul>
        </div>
        <span style={{ fontSize: "var(--text-xs)", color: "rgba(255,255,255,.58)" }}>
          Authorised use only. Activity is logged against your account.
        </span>
      </div>

      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", padding: "var(--space-8)" }}>
        <div style={{ width: "100%", maxWidth: 340, display: "flex", flexDirection: "column", gap: "var(--space-5)" }}>
          <div>
            <h2 style={{ fontSize: "var(--text-xl)", fontWeight: 600 }}>Sign in</h2>
            <p style={{ margin: "4px 0 0", fontSize: "var(--text-sm)", color: "var(--text-muted)" }}>Use your corporate account.</p>
          </div>

          {failed ? <Alert tone="danger" title="Sign-in failed">Check your username and password, or use single sign-on.</Alert> : null}

          <Button variant="primary" size="lg" fullWidth iconLeft="building-2" onClick={onSignIn}>Continue with single sign-on</Button>

          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)" }}>
            <span style={{ flex: 1, height: 1, background: "var(--border-default)" }} />
            <span style={{ fontSize: "var(--text-xs)", color: "var(--text-subtle)" }}>or</span>
            <span style={{ flex: 1, height: 1, background: "var(--border-default)" }} />
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: "var(--gap-field)" }}>
            <FormField label="Username" htmlFor="un" maxWidth="100%">
              <Input id="un" placeholder="first.last" autoComplete="username" />
            </FormField>
            <FormField label="Password" htmlFor="pw" maxWidth="100%">
              <Input id="pw" type="password" placeholder="••••••••••" autoComplete="current-password" />
            </FormField>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "var(--space-3)" }}>
              <Checkbox label="Keep me signed in" defaultChecked={false} />
              <Button variant="link" size="sm">Forgot password</Button>
            </div>
            <Button size="lg" fullWidth onClick={() => setFailed(true)}>Sign in</Button>
          </div>

          <p style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--text-muted)", textWrap: "pretty" }}>
            Access is granted through the network operations group. Contact the service desk if your role has changed.
          </p>
        </div>
      </div>
    </div>
  );
}

/* Settings / admin: tabbed preferences and a role table. */
function SettingsScreen({ theme, onToggleTheme }) {
  const [tab, setTab] = React.useState("preferences");
  return (
    <React.Fragment>
      <Tabs
        activeId={tab}
        onChange={setTab}
        tabs={[
          { id: "preferences", label: "Preferences" },
          { id: "notifications", label: "Notifications" },
          { id: "roles", label: "Roles & access", count: 5 },
          { id: "integrations", label: "Integrations" },
        ]}
      />

      {tab === "preferences" ? (
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 300px", gap: "var(--space-6)", alignItems: "start" }}>
          <Card title="Display" footer={<React.Fragment><Button>Reset</Button><Button variant="primary">Save preferences</Button></React.Fragment>}>
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--gap-field)" }}>
              <FormField label="Theme" hint="Dark theme uses the same tokens with re-pointed surfaces.">
                <div style={{ paddingTop: 2 }}>
                  <Switch checked={theme === "dark"} onChange={onToggleTheme} label="Dark theme" description="Applies immediately across the portal." />
                </div>
              </FormField>
              <FormField label="Default landing screen" htmlFor="ls" maxWidth={280}>
                <Select id="ls" defaultValue="Dashboard" options={["Dashboard", "Task assignments", "Circuits & WAN", "Compliance reports"]} />
              </FormField>
              <FormField label="Table density" htmlFor="td" maxWidth={280} hint="Compact fits roughly 30% more rows per screen.">
                <Select id="td" defaultValue="Comfortable" options={["Comfortable", "Compact"]} />
              </FormField>
              <FormField label="Timezone" htmlFor="tz" maxWidth={280}>
                <Select id="tz" defaultValue="America/Chicago" options={["UTC", "America/Chicago", "America/New_York", "America/Denver", "America/Los_Angeles"]} />
              </FormField>
            </div>
          </Card>
          <Card title="Session">
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)", fontSize: "var(--text-sm)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: "var(--space-3)" }}><span style={{ color: "var(--text-muted)" }}>Signed in as</span><span>D. Okafor</span></div>
              <div style={{ display: "flex", justifyContent: "space-between", gap: "var(--space-3)" }}><span style={{ color: "var(--text-muted)" }}>Role</span><Badge tone="brand">Network admin</Badge></div>
              <div style={{ display: "flex", justifyContent: "space-between", gap: "var(--space-3)" }}><span style={{ color: "var(--text-muted)" }}>Method</span><span>Single sign-on</span></div>
              <div style={{ display: "flex", justifyContent: "space-between", gap: "var(--space-3)" }}><span style={{ color: "var(--text-muted)" }}>Expires</span><span style={{ fontFamily: "var(--font-mono)" }}>17:40 today</span></div>
              <Button variant="secondary" iconLeft="log-out" fullWidth>Sign out</Button>
            </div>
          </Card>
        </div>
      ) : null}

      {tab === "notifications" ? (
        <Card title="Notifications" subtitle="Changes take effect immediately" footer={<Button variant="primary">Done</Button>}>
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)", maxWidth: 560 }}>
            <Switch defaultChecked label="Task assigned to me" description="Email as it happens." />
            <Switch defaultChecked label="Report due in 7 days" description="One digest per report, sent at 07:00." />
            <Switch defaultChecked label="Report marked overdue" description="Email plus in-portal banner." />
            <Switch label="Configuration drift detected" description="Can be noisy on sites under active change." />
            <Switch label="Weekly compliance digest" description="Sent Mondays at 07:00 local." />
          </div>
        </Card>
      ) : null}

      {tab === "roles" ? (
        <Card title="Roles & access" subtitle="Managed in the corporate directory; shown here read-only" padding="none" actions={<Button size="sm" variant="ghost" iconLeft="external-link">Open directory</Button>}>
          <DataTable
            columns={[
              { key: "role", header: "Role", width: 180 },
              { key: "members", header: "Members", align: "right", mono: true, width: 100, sortAccessor: (r) => Number(r.members) },
              { key: "scope", header: "Scope", wrap: true },
              { key: "can", header: "Permissions", wrap: true, muted: true },
              { key: "status", header: "Status", width: 130, render: (r) => <StatusPill status={r.status} /> },
            ]}
            rows={[
              { id: 1, role: "Network admin", members: 8, scope: "All sites", can: "Create, edit and decommission assets; submit reports", status: "active" },
              { id: 2, role: "Compliance reviewer", members: 5, scope: "All frameworks", can: "Accept or return submissions; export evidence packs", status: "active" },
              { id: 3, role: "Site technician", members: 41, scope: "Assigned site only", can: "Edit location fields; complete assigned tasks", status: "active" },
              { id: 4, role: "Auditor (external)", members: 3, scope: "Approved reports only", can: "Read approved reports and evidence", status: "active" },
              { id: 5, role: "Read only", members: 112, scope: "All sites", can: "View inventory and reports", status: "active" },
            ]}
          />
        </Card>
      ) : null}

      {tab === "integrations" ? (
        <Card title="Integrations" padding="none">
          <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {[["NetBox", "boxes", "Inventory sync every 15 minutes", "active"], ["LibreNMS", "radio-tower", "Availability and interface counters", "active"], ["Change tickets", "ticket", "Ticket validation on inventory edits", "active"], ["Credential vault", "key-round", "Read-only token for config pulls", "exception"]].map((i) => (
              <li key={i[0]} style={{ display: "flex", alignItems: "center", gap: "var(--space-3)", padding: "var(--space-3) var(--pad-card)", borderBottom: "var(--border-width) solid var(--border-subtle)" }}>
                <span style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 30, height: 30, flex: "0 0 auto", background: "var(--navy-50)", color: "var(--navy-600)", border: "1px solid var(--navy-100)", borderRadius: "var(--radius-md)" }}>
                  <Icon name={i[1]} size={15} />
                </span>
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ display: "block", fontSize: "var(--text-base)", fontWeight: 500 }}>{i[0]}</span>
                  <span style={{ display: "block", fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>{i[2]}</span>
                </span>
                <StatusPill status={i[3]} />
                <IconButton icon="settings" label={"Configure " + i[0]} size="sm" />
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </React.Fragment>
  );
}

Object.assign(window, { LoginScreen, SettingsScreen });
