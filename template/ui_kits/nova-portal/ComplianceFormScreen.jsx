const { Card, Button, FormField, Input, Select, Textarea, Checkbox, Radio, FileUpload, Alert, Toast } = window.PortalDesignSystem_986bf0;

/* Single-page compliance submission form: the portal's canonical form layout.
   Labels above fields, 560px field column, validation summary Alert on submit. */
function ComplianceFormScreen({ onNavigate }) {
  const [scope, setScope] = React.useState("region");
  const [submitted, setSubmitted] = React.useState(false);
  const [errors, setErrors] = React.useState(false);
  const [period, setPeriod] = React.useState("");
  const [files, setFiles] = React.useState([{ name: "q3-firewall-rules-chi01.csv", size: "842 KB" }]);

  const submit = () => {
    if (!period) { setErrors(true); return; }
    setErrors(false);
    setSubmitted(true);
  };

  return (
    <React.Fragment>
      {errors ? (
        <Alert tone="danger" title="1 field needs attention">Reporting period is required before this submission can be queued for review.</Alert>
      ) : null}

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 280px", gap: "var(--space-6)", alignItems: "start" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
          <Card
            title="Report details"
            footer={<React.Fragment><Button onClick={() => onNavigate("reports")}>Cancel</Button><Button variant="secondary">Save draft</Button><Button variant="primary" onClick={submit}>Submit for review</Button></React.Fragment>}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--gap-field)" }}>
              <FormField label="Report type" htmlFor="rt" required hint="Determines which controls and evidence are required.">
                <Select id="rt" defaultValue="fw" options={[
                  { value: "fw", label: "Quarterly firewall rule review" },
                  { value: "acc", label: "Privileged access review" },
                  { value: "pci", label: "PCI segmentation attestation" },
                  { value: "cfg", label: "Device configuration standard conformance" },
                  { value: "bkp", label: "Backup and restore verification" },
                ]} />
              </FormField>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-4)", maxWidth: "var(--field-max)" }}>
                <FormField label="Reporting period" htmlFor="pd" required error={errors && !period ? "Select a reporting period." : undefined} maxWidth="100%">
                  <Select id="pd" invalid={errors && !period} placeholder="Select a period" value={period} onChange={(e) => setPeriod(e.target.value)} options={["Q3 2026", "Q2 2026", "H2 2026", "Sep 2026"]} />
                </FormField>
                <FormField label="Due date" htmlFor="dd" maxWidth="100%" hint="Set by the framework calendar.">
                  <Input id="dd" mono defaultValue="2026-09-30" disabled />
                </FormField>
              </div>

              <FormField label="Scope" required hint="Wider scope pulls in more assets and more required evidence.">
                <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)", paddingTop: 2 }}>
                  <Radio name="scope" value="site" checked={scope === "site"} onChange={() => setScope("site")} label="Single site" description="One facility and the assets terminating there." />
                  <Radio name="scope" value="region" checked={scope === "region"} onChange={() => setScope("region")} label="Whole region" description="All sites in the selected region — 23 firewalls." />
                  <Radio name="scope" value="global" checked={scope === "global"} onChange={() => setScope("global")} label="All managed assets" description="4,182 devices. Expect a long evidence collection window." />
                </div>
              </FormField>

              {scope === "region" ? (
                <FormField label="Region" htmlFor="rg" required maxWidth={280}>
                  <Select id="rg" defaultValue="South" options={["Midwest", "Northeast", "South", "West"]} />
                </FormField>
              ) : null}

              <FormField label="Reviewer" htmlFor="rv" required hint="Must be someone other than the asset owner.">
                <Select id="rv" defaultValue="A. Bello" options={["A. Bello", "M. Ruiz", "D. Okafor", "J. Lindqvist", "S. Haddad"]} />
              </FormField>

              <FormField label="Supporting evidence" hint="Rule exports, screenshots of approvals, sign-off emails. 25 MB per file.">
                <FileUpload files={files} onRemove={(f) => setFiles(files.filter((x) => x.name !== f.name))} />
              </FormField>

              <FormField label="Summary of findings" htmlFor="sf" optional hint="Visible to the auditor alongside the evidence pack.">
                <Textarea id="sf" rows={4} placeholder="Note any exceptions, compensating controls and remediation dates." />
              </FormField>

              <FormField label="Attestation" required>
                <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)", paddingTop: 2 }}>
                  <Checkbox label="The evidence attached is complete for the stated scope and period." />
                  <Checkbox label="Exceptions listed above have an approved compensating control." />
                  <Checkbox label="Notify the reviewer by email on submission." defaultChecked />
                </div>
              </FormField>
            </div>
          </Card>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)", position: "sticky", top: 0 }}>
          <Card title="Required evidence">
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: "var(--text-sm)", color: "var(--text-body)", display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
              <li>Firewall rule export per in-scope device</li>
              <li>Reviewer sign-off for each ruleset</li>
              <li>Justification for any rule older than 12 months</li>
              <li>Change ticket references for rule additions</li>
            </ul>
          </Card>
          <Card title="What happens next">
            <ol style={{ margin: 0, paddingLeft: 18, fontSize: "var(--text-sm)", color: "var(--text-body)", display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
              <li>Reviewer has five working days to accept or return the submission.</li>
              <li>Returned submissions reopen as a task assigned to you.</li>
              <li>Accepted submissions are locked and added to the audit pack.</li>
            </ol>
          </Card>
        </div>
      </div>

      {submitted ? (
        <div style={{ position: "fixed", right: "var(--space-6)", bottom: "var(--space-6)", zIndex: 1100 }}>
          <Toast tone="success" title="Submitted as REP-2294" onDismiss={() => setSubmitted(false)} action={<Button variant="link" size="sm" onClick={() => onNavigate("reports")}>View register</Button>}>
            A. Bello has been notified and has until 22 September to review.
          </Toast>
        </div>
      ) : null}
    </React.Fragment>
  );
}

Object.assign(window, { ComplianceFormScreen });
