const { Card, Button, FormField, Input, Select, Checkbox, Textarea, Icon, Alert, ProgressMeter, Toast, DataTable } = window.PortalDesignSystem_986bf0;

const STEPS = [
  { id: 1, label: "Asset type", hint: "What is being added" },
  { id: 2, label: "Identity", hint: "Names and identifiers" },
  { id: 3, label: "Location", hint: "Site and rack" },
  { id: 4, label: "Addressing", hint: "IP and VLAN" },
  { id: 5, label: "Review", hint: "Confirm and create" },
];

/* Multi-step intake wizard. Steps are a left rail, not a top stepper — the portal
   has long field lists and the rail keeps the vertical rhythm of a normal form. */
function MultiStepFormScreen({ onNavigate }) {
  const [step, setStep] = React.useState(2);
  const [created, setCreated] = React.useState(false);

  return (
    <React.Fragment>
      <div style={{ display: "grid", gridTemplateColumns: "232px minmax(0,1fr)", gap: "var(--space-6)", alignItems: "start" }}>
        <Card padding="none">
          <ol style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {STEPS.map((s) => {
              const state = s.id < step ? "done" : s.id === step ? "current" : "todo";
              return (
                <li key={s.id}>
                  <button
                    type="button"
                    onClick={() => setStep(s.id)}
                    style={{
                      display: "flex", alignItems: "flex-start", gap: "var(--space-3)", width: "100%",
                      padding: "var(--space-3) var(--pad-card)", textAlign: "left", cursor: "pointer",
                      background: state === "current" ? "var(--surface-selected)" : "transparent",
                      border: 0, borderBottom: "var(--border-width) solid var(--border-subtle)",
                      borderLeft: "var(--border-accent-width) solid " + (state === "current" ? "var(--navy-700)" : "transparent"),
                    }}
                  >
                    <span
                      style={{
                        display: "inline-flex", alignItems: "center", justifyContent: "center",
                        width: 20, height: 20, flex: "0 0 auto", marginTop: 1,
                        borderRadius: "var(--radius-pill)",
                        fontSize: "var(--text-2xs)", fontWeight: 600,
                        background: state === "done" ? "var(--success-ink)" : state === "current" ? "var(--navy-700)" : "var(--gray-100)",
                        color: state === "todo" ? "var(--text-muted)" : "#fff",
                        border: state === "todo" ? "1px solid var(--border-default)" : "none",
                      }}
                    >
                      {state === "done" ? <Icon name="check" size={12} strokeWidth={3} /> : s.id}
                    </span>
                    <span style={{ minWidth: 0 }}>
                      <span style={{ display: "block", fontSize: "var(--text-sm)", fontWeight: state === "current" ? 600 : 500, color: state === "todo" ? "var(--text-muted)" : "var(--text-body)" }}>{s.label}</span>
                      <span style={{ display: "block", fontSize: "var(--text-xs)", color: "var(--text-muted)", marginTop: 1 }}>{s.hint}</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
          <div style={{ padding: "var(--space-3) var(--pad-card)" }}>
            <ProgressMeter value={step} max={STEPS.length} size="sm" valueText={"Step " + step + " of " + STEPS.length} />
          </div>
        </Card>

        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
          {step === 4 ? (
            <Alert tone="info" title="10.42.24.0/22 has 412 free addresses">Allocations are reserved for 24 hours while the record is in draft.</Alert>
          ) : null}

          <Card
            title={STEPS[step - 1].label}
            subtitle={step === 5 ? "Check the record before it is written to inventory" : "All fields are required unless marked optional"}
            footer={
              <React.Fragment>
                <Button onClick={() => onNavigate("devices")}>Cancel</Button>
                <div style={{ flex: 1 }} />
                <Button disabled={step === 1} iconLeft="chevron-left" onClick={() => setStep(Math.max(1, step - 1))}>Back</Button>
                {step < STEPS.length ? (
                  <Button variant="primary" iconRight="chevron-right" onClick={() => setStep(step + 1)}>Continue</Button>
                ) : (
                  <Button variant="primary" iconLeft="check" onClick={() => setCreated(true)}>Create asset</Button>
                )}
              </React.Fragment>
            }
          >
            {step === 1 ? (
              <div style={{ display: "flex", flexDirection: "column", gap: "var(--gap-field)" }}>
                <FormField label="Asset class" htmlFor="ac" required hint="Determines the remaining fields and the applicable configuration standard.">
                  <Select id="ac" defaultValue="Router or switch" options={["Circuit / WAN link", "Router or switch", "Firewall", "Wireless AP", "Site / facility", "IP block or VLAN", "License"]} />
                </FormField>
                <FormField label="Management model" required>
                  <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)", paddingTop: 2 }}>
                    <Checkbox defaultChecked label="Fully managed by the network team" description="Included in nightly config checks and compliance scope." />
                    <Checkbox label="Monitored only" description="Polled for availability; excluded from configuration standards." />
                  </div>
                </FormField>
              </div>
            ) : null}

            {step === 2 ? (
              <div style={{ display: "flex", flexDirection: "column", gap: "var(--gap-field)" }}>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-4)", maxWidth: "var(--field-max)" }}>
                  <FormField label="Hostname" htmlFor="hn" required hint="site-role-nn, lower case." maxWidth="100%">
                    <Input id="hn" mono placeholder="phx01-sw-02" />
                  </FormField>
                  <FormField label="Serial number" htmlFor="sn" required maxWidth="100%">
                    <Input id="sn" mono placeholder="FDO2416R0AB" />
                  </FormField>
                </div>
                <FormField label="Model" htmlFor="md" required>
                  <Select id="md" placeholder="Select a model" options={["Cisco C8500-12X", "Cisco C9300-48P", "Arista 7050SX3", "Juniper MX204", "Palo Alto PA-3440"]} />
                </FormField>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-4)", maxWidth: "var(--field-max)" }}>
                  <FormField label="Role" htmlFor="rl" required maxWidth="100%">
                    <Select id="rl" defaultValue="Access switch" options={["Core router", "Distribution switch", "Access switch", "Perimeter firewall", "Wireless controller"]} />
                  </FormField>
                  <FormField label="Owner" htmlFor="ow" required maxWidth="100%">
                    <Select id="ow" defaultValue="D. Okafor" options={["D. Okafor", "M. Ruiz", "A. Bello", "J. Lindqvist"]} />
                  </FormField>
                </div>
                <FormField label="Asset tag" htmlFor="tg" optional hint="Finance asset tag, if one has been issued." maxWidth={280}>
                  <Input id="tg" mono placeholder="AT-0000000" />
                </FormField>
              </div>
            ) : null}

            {step === 3 ? (
              <div style={{ display: "flex", flexDirection: "column", gap: "var(--gap-field)" }}>
                <FormField label="Site" htmlFor="st" required>
                  <Select id="st" defaultValue="PHX-01" options={["CHI-01", "DAL-03", "ATL-02", "BOS-01", "NYC-04", "SEA-02", "PHX-01", "DEN-01"]} />
                </FormField>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "var(--space-4)", maxWidth: "var(--field-max)" }}>
                  <FormField label="Room" htmlFor="rm" required maxWidth="100%"><Input id="rm" defaultValue="MDF" /></FormField>
                  <FormField label="Rack" htmlFor="rk" required maxWidth="100%"><Input id="rk" mono defaultValue="R14" /></FormField>
                  <FormField label="Rack unit" htmlFor="ru" required maxWidth="100%"><Input id="ru" mono defaultValue="22" suffix="U" /></FormField>
                </div>
                <FormField label="Access notes" htmlFor="an" optional hint="Escort requirements, badge zones, loading dock hours.">
                  <Textarea id="an" rows={3} />
                </FormField>
              </div>
            ) : null}

            {step === 4 ? (
              <div style={{ display: "flex", flexDirection: "column", gap: "var(--gap-field)" }}>
                <FormField label="IP block" htmlFor="ib" required hint="Management subnet for the site.">
                  <Select id="ib" defaultValue="10.42.24.0/22" options={["10.42.24.0/22", "10.60.40.0/22", "10.108.8.0/22"]} />
                </FormField>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-4)", maxWidth: "var(--field-max)" }}>
                  <FormField label="Management IP" htmlFor="mi" required maxWidth="100%"><Input id="mi" mono defaultValue="10.42.24.18" /></FormField>
                  <FormField label="Management VLAN" htmlFor="mv" required maxWidth="100%"><Input id="mv" mono defaultValue="412" /></FormField>
                </div>
                <FormField label="SNMP profile" htmlFor="sp" required>
                  <Select id="sp" defaultValue="v3-auth-priv" options={["v3-auth-priv", "v3-auth-nopriv", "v2c-readonly (deprecated)"]} />
                </FormField>
              </div>
            ) : null}

            {step === 5 ? (
              <DataTable
                compact
                columns={[{ key: "field", header: "Field", width: 200, muted: true }, { key: "value", header: "Value", mono: true }]}
                rows={[
                  { id: 1, field: "Asset class", value: "Router or switch" },
                  { id: 2, field: "Hostname", value: "phx01-sw-02" },
                  { id: 3, field: "Model", value: "Cisco C9300-48P" },
                  { id: 4, field: "Role", value: "Access switch" },
                  { id: 5, field: "Site / rack", value: "PHX-01 · MDF · R14 · 22U" },
                  { id: 6, field: "Management IP", value: "10.42.24.18" },
                  { id: 7, field: "Management VLAN", value: "412" },
                  { id: 8, field: "SNMP profile", value: "v3-auth-priv" },
                  { id: 9, field: "Compliance scope", value: "CS-11, CS-04" },
                ]}
              />
            ) : null}
          </Card>
        </div>
      </div>

      {created ? (
        <div style={{ position: "fixed", right: "var(--space-6)", bottom: "var(--space-6)", zIndex: 1100 }}>
          <Toast tone="success" title="phx01-sw-02 created" onDismiss={() => setCreated(false)} action={<Button variant="link" size="sm" onClick={() => onNavigate("devices")}>Open record</Button>}>
            Added to inventory and queued for its first configuration check tonight.
          </Toast>
        </div>
      ) : null}
    </React.Fragment>
  );
}

Object.assign(window, { MultiStepFormScreen });
