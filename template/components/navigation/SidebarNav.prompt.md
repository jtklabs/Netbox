The portal's left rail — present on every authenticated screen, frosted over the ambient field.

```jsx
<SidebarNav
  brand="Nova"
  activeId="inventory-circuits"
  onNavigate={setScreen}
  sections={[
    { id:"ops", label:"Operations", items:[{id:"dashboard",label:"Dashboard",icon:"layout-dashboard"}] },
    { id:"inv", label:"Inventory", items:[{id:"inventory-circuits",label:"Circuits",icon:"cable"}] },
    { id:"ext", label:"Tools", items:[{id:"grafana",label:"Grafana",icon:"activity",external:true}] },
  ]}
  footer={<ThemeToggle/>}
/>
```

Three levels: uppercase collapsible section headings, then items, then nested child items —
give an item an `items` array and it becomes a disclosure row instead of a link
(`Inventory → Global → Cisco`). A group opens itself when the active route is under it; each
level steps in 14px and drops a type size, so the hierarchy reads without connector lines.
Three levels is the practical limit before the indent runs out of rail. The active item is a filled pill
with a 3px crimson leading marker — that marker is the one place crimson appears in the chrome. Put
counts on items that accumulate work (open tasks, overdue reports) and set `countTone="danger"` when
the count is overdue. Links that leave the portal get `external`.

**It collapses itself.** The toggle sits in the brand row and the state persists to `localStorage`
under `storageKey`. Collapsed, the rail is a 56px icon-only strip: section labels become hairline
rules, counts become a corner dot, nested groups are hidden and clicking one expands the rail
instead — so **every top-level item needs an `icon`** if users can collapse it. Pass `collapsed` +
`onCollapsedChange` only when something outside the rail must drive the state,
`collapsible={false}` to remove the toggle, or `storageKey=""` not to remember it.

**`footer` takes a function** when it has to survive that: `footer={({ collapsed }) => …}`. A fixed
node is clipped at 56px, so anything with more than one element wants the function form.

**Branding.** `logo` takes an image URL (or your own node) and replaces the wordmark;
`logoCollapsed` is the mark for the 56px rail. The brand slot is a fixed 28px-tall box either way,
so the swap costs no layout shift. With no logo it sets `brand` in type — no logo file was supplied
with this system, so that is the current state.
