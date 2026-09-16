window.NovaData = {
  navSections: [
    {
      id: "ops",
      items: [
        { id: "dashboard", label: "Dashboard", icon: "layout-dashboard" },
        { id: "sites", label: "Sites & facilities", icon: "building-2" },
      ],
    },
    {
      id: "inventory", label: "Inventory",
      items: [
        { id: "circuits", label: "Circuits & WAN", icon: "cable" },
        { id: "devices", label: "Routers & switches", icon: "server" },
        /* Third level: Inventory → Global → Cisco. Groups open themselves when something
           under them is the active route. */
        {
          id: "global", label: "Global", icon: "globe",
          items: [
            { id: "vendor-cisco", label: "Cisco" },
            { id: "vendor-juniper", label: "Juniper" },
            { id: "vendor-arista", label: "Arista" },
          ],
        },
        { id: "firewalls", label: "Firewalls", icon: "shield" },
        { id: "wireless", label: "Wireless APs", icon: "wifi" },
        { id: "addressing", label: "IP blocks & VLANs", icon: "network" },
        { id: "carriers", label: "Carriers & contracts", icon: "file-text" },
        { id: "licenses", label: "Licenses", icon: "key-round" },
      ],
    },
    {
      id: "compliance", label: "Compliance",
      items: [
        { id: "reports", label: "Reports", icon: "shield-check", count: 3, countTone: "danger" },
        { id: "submit", label: "Submit a report", icon: "file-plus-2" },
        { id: "standards", label: "Config standards", icon: "list-checks" },
      ],
    },
    {
      id: "work", label: "Work",
      items: [
        { id: "tasks", label: "Task assignments", icon: "clipboard-list", count: 12 },
        { id: "intake", label: "New asset intake", icon: "package-plus" },
      ],
    },
    {
      id: "tools", label: "Linked tools",
      items: [
        { id: "tool-launcher", label: "All tools", icon: "layout-grid" },
        { id: "t-grafana", label: "Grafana", icon: "activity", external: true },
        { id: "t-netbox", label: "NetBox", icon: "boxes", external: true },
        { id: "t-jira", label: "Change tickets", icon: "ticket", external: true },
      ],
    },
    { id: "admin", label: "Administration", items: [{ id: "settings", label: "Settings", icon: "settings" }], defaultOpen: false },
  ],

  circuits: [
    { id: "CKT-40182", carrier: "Lumen", site: "CHI-01", region: "Midwest", bandwidth: "1,000 Mbps", ip: "10.42.8.1", status: "compliant", term: "2027-03-31", monthly: "$4,180" },
    { id: "CKT-40219", carrier: "Zayo", site: "DAL-03", region: "South", bandwidth: "500 Mbps", ip: "10.42.16.1", status: "due soon", term: "2026-11-30", monthly: "$2,640" },
    { id: "CKT-40244", carrier: "AT&T", site: "ATL-02", region: "South", bandwidth: "10,000 Mbps", ip: "10.108.2.1", status: "overdue", term: "2028-06-30", monthly: "$11,900" },
    { id: "CKT-40301", carrier: "Cogent", site: "DEN-01", region: "West", bandwidth: "250 Mbps", ip: "10.60.4.1", status: "decommissioned", term: "2026-09-30", monthly: "$980" },
    { id: "CKT-40355", carrier: "Lumen", site: "BOS-01", region: "Northeast", bandwidth: "1,000 Mbps", ip: "10.12.8.1", status: "compliant", term: "2027-01-31", monthly: "$4,320" },
    { id: "CKT-40388", carrier: "Verizon", site: "NYC-04", region: "Northeast", bandwidth: "2,000 Mbps", ip: "10.12.32.1", status: "exception", term: "2027-08-31", monthly: "$6,750" },
    { id: "CKT-40402", carrier: "Zayo", site: "SEA-02", region: "West", bandwidth: "1,000 Mbps", ip: "10.60.24.1", status: "compliant", term: "2027-05-31", monthly: "$4,090" },
    { id: "CKT-40417", carrier: "Comcast", site: "PHX-01", region: "West", bandwidth: "500 Mbps", ip: "10.60.40.1", status: "due soon", term: "2026-12-31", monthly: "$1,860" },
  ],

  devices: [
    { id: "chi01-cr-01", model: "Cisco C8500-12X", role: "Core router", site: "CHI-01", ip: "10.42.0.11", os: "IOS-XE 17.12.3", status: "compliant" },
    { id: "chi01-sw-04", model: "Arista 7050SX3", role: "Access switch", site: "CHI-01", ip: "10.42.0.34", os: "EOS 4.31.2F", status: "exception" },
    { id: "dal03-fw-01", model: "Palo Alto PA-3440", role: "Perimeter firewall", site: "DAL-03", ip: "10.42.16.9", os: "PAN-OS 11.1.2", status: "overdue" },
    { id: "atl02-cr-02", model: "Juniper MX204", role: "Core router", site: "ATL-02", ip: "10.108.0.12", os: "Junos 22.4R3", status: "compliant" },
    { id: "bos01-ap-17", model: "Aruba AP-635", role: "Wireless AP", site: "BOS-01", ip: "10.12.9.17", os: "ArubaOS 10.5", status: "compliant" },
  ],

  reports: [
    { id: "REP-2291", name: "Quarterly firewall rule review", framework: "SOX", period: "Q3 2026", owner: "M. Ruiz", due: "2026-09-30", status: "overdue", coverage: 74 },
    { id: "REP-2288", name: "Privileged access review", framework: "SOX", period: "Q3 2026", owner: "A. Bello", due: "2026-09-30", status: "in review", coverage: 100 },
    { id: "REP-2284", name: "PCI segmentation attestation", framework: "PCI DSS 4.0", period: "H2 2026", owner: "D. Okafor", due: "2026-10-15", status: "due soon", coverage: 88 },
    { id: "REP-2279", name: "Device configuration standard conformance", framework: "Internal CS-11", period: "Sep 2026", owner: "J. Lindqvist", died: "", due: "2026-09-20", status: "submitted", coverage: 96 },
    { id: "REP-2271", name: "Change-control evidence sample", framework: "SOX", period: "Aug 2026", owner: "M. Ruiz", due: "2026-09-05", status: "approved", coverage: 100 },
    { id: "REP-2265", name: "Backup and restore verification", framework: "Internal CS-04", period: "Aug 2026", owner: "S. Haddad", due: "2026-09-05", status: "approved", coverage: 100 },
  ],

  tasks: [
    { id: "TSK-8841", title: "Collect firewall rule export for DAL-03", assignee: "M. Ruiz", related: "REP-2291", due: "2026-09-17", priority: "High", status: "overdue" },
    { id: "TSK-8846", title: "Confirm decommission of CKT-40301", assignee: "D. Okafor", related: "CKT-40301", due: "2026-09-18", priority: "Medium", status: "pending" },
    { id: "TSK-8851", title: "Upload access review sign-off (Q3)", assignee: "A. Bello", related: "REP-2288", due: "2026-09-19", priority: "High", status: "in review" },
    { id: "TSK-8853", title: "Remediate NTP drift on chi01-sw-04", assignee: "J. Lindqvist", related: "chi01-sw-04", due: "2026-09-22", priority: "Medium", status: "pending" },
    { id: "TSK-8858", title: "Renew Zayo contract for SEA-02", assignee: "S. Haddad", related: "CKT-40402", due: "2026-09-30", priority: "Low", status: "planned" },
    { id: "TSK-8861", title: "Validate VLAN 412 documentation", assignee: "D. Okafor", related: "10.42.24.0/22", due: "2026-10-02", priority: "Low", status: "planned" },
  ],

  tools: [
    { id: "grafana", name: "Grafana", icon: "activity", group: "Monitoring", description: "Link utilisation, latency and interface error dashboards." },
    { id: "netbox", name: "NetBox", icon: "boxes", group: "Source of truth", description: "Upstream DCIM and IPAM records that feed this portal." },
    { id: "librenms", name: "LibreNMS", icon: "radio-tower", group: "Monitoring", description: "SNMP polling, alerting and device availability." },
    { id: "jira", name: "Change tickets", icon: "ticket", group: "Change", description: "Raise and track change requests and approvals." },
    { id: "confluence", name: "Runbooks", icon: "book-open", group: "Change", description: "Site runbooks, escalation paths and maintenance windows." },
    { id: "vault", name: "Credential vault", icon: "key-round", group: "Access", description: "Device credentials and API tokens. Requires a second factor." },
    { id: "smartsheet", name: "Capacity plan", icon: "table-2", group: "Planning", description: "Rolling twelve-month bandwidth and hardware plan." },
    { id: "backups", name: "Config archive", icon: "hard-drive-download", group: "Source of truth", description: "Nightly device configuration snapshots and diffs." },
  ],

  activity: [
    { who: "M. Ruiz", what: "submitted evidence for REP-2288", when: "14 min ago", icon: "file-check" },
    { who: "Discovery job", what: "added 3 devices at PHX-01", when: "1 hr ago", icon: "refresh-cw" },
    { who: "D. Okafor", what: "closed TSK-8837", when: "2 hr ago", icon: "circle-check" },
    { who: "J. Lindqvist", what: "flagged config drift on chi01-sw-04", when: "4 hr ago", icon: "git-compare" },
    { who: "System", what: "marked REP-2291 overdue", when: "Yesterday 17:00", icon: "triangle-alert" },
  ],
};
