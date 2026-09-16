# Nova portal — UI kit

A click-through recreation of the Nova network operations portal. Open `index.html`.

**Important:** this is a recreation built from a written description of the portal, not from its Django
source. No codebase was attached to this project. Layout conventions (left rail with nested sections,
labels-above-fields forms, comfortable table density) come from the answers on record in the root
`readme.md`; everything else — copy, record data, screen composition — is a designed proposal, not a
copy of what exists today. Attach the repository and these screens can be corrected against the real
templates.

## Flow

Sign-in lands on the dashboard. From there:

| Screen | File | Reached from |
| --- | --- | --- |
| Login | `AuthAndSettingsScreens.jsx` | first load |
| Dashboard | `DashboardScreen.jsx` | sidebar → Dashboard |
| Inventory list | `InventoryListScreen.jsx` | sidebar → any Inventory item |
| Inventory detail (3 tabs) | `InventoryDetailScreen.jsx` | click any table row |
| Compliance register | `ComplianceReportScreen.jsx` | sidebar → Reports / Config standards |
| Compliance report detail | `ComplianceReportScreen.jsx` | click a report row |
| Compliance submission form | `ComplianceFormScreen.jsx` | sidebar → Submit a report |
| Task list | `TaskScreens.jsx` | sidebar → Task assignments |
| Task detail | `TaskScreens.jsx` | click any task row |
| Multi-step intake | `MultiStepFormScreen.jsx` | sidebar → New asset intake, or "Add asset" |
| Tool launcher | `ToolLauncherScreen.jsx` | sidebar → Linked tools |
| Settings (4 tabs) | `AuthAndSettingsScreens.jsx` | sidebar → Administration → Settings |

`AppShell.jsx` is the chrome every authenticated screen sits inside. `data.js` holds the fake records.

## What actually works

Filtering and search on the inventory list; sorting by any column; row selection with a bulk action
bar; the reassign dialog and its toast; theme toggle (light/dark, in the sidebar footer and in
Settings); tab switching on detail screens; wizard step navigation; form validation on the compliance
submission (submit with no period selected).

## Conventions the kit demonstrates

- One `primary` button per screen, in the page header or a card footer.
- Every table lives in a `Card` with `padding="none"`; pagination sits below it, inside the same card.
- Identifier columns are mono with tabular figures. Numbers right-align.
- Lifecycle state is always a `StatusPill`, never coloured text.
- Validation summaries are an `Alert` at the top of the content column; confirmations are a `Toast`
  bottom-right.
- Detail screens are a 2:1 grid — record body left, status and related records right.
