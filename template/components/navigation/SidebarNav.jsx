import React from "react";
import { Icon } from "../core/Icon.jsx";

/* The portal's left rail, on frost.

   Three levels of structure: uppercase sections, items, and nested child items — e.g.
   Inventory → Global → Cisco. Nesting is recursive, so deeper is possible, but three levels
   is the practical limit before the indent runs out of rail. A group that contains the active
   item opens itself.

   Collapsing: the rail runs uncontrolled by default — pass `collapsible={false}` to remove
   the toggle, or drive it from outside with `collapsed` + `onCollapsedChange`. Collapsed, it
   shows top-level icons only at 56px, so give every top-level item an icon if you intend to
   let users collapse it; clicking a group there expands the rail rather than opening a
   submenu with nowhere to go. The chosen state persists per `storageKey`.

   `footer` may be a node or a function of ({ collapsed }) — use the function form when the
   footer has to shed parts at 56px. */
export function SidebarNav({
  brand = "Nova", logo, logoCollapsed, sections = [], activeId, onNavigate, footer,
  collapsible = true, collapsed, onCollapsedChange, storageKey = "nova.nav.collapsed",
  style, ...rest
}) {
  const controlled = collapsed != null;
  const [selfCollapsed, setSelfCollapsed] = React.useState(() => {
    if (typeof window === "undefined" || !storageKey) return false;
    try { return window.localStorage.getItem(storageKey) === "1"; } catch { return false; }
  });
  const isCollapsed = controlled ? collapsed : selfCollapsed;

  const setCollapsed = (next) => {
    if (!controlled) {
      setSelfCollapsed(next);
      if (storageKey) { try { window.localStorage.setItem(storageKey, next ? "1" : "0"); } catch {} }
    }
    onCollapsedChange && onCollapsedChange(next);
  };

  const initial = {};
  sections.forEach((s) => { initial[s.id] = s.defaultOpen !== false; });
  const [open, setOpen] = React.useState(initial);
  const toggleSection = (id) => setOpen((o) => ({ ...o, [id]: !o[id] }));

  return (
    <nav
      aria-label="Primary"
      data-collapsed={isCollapsed ? "" : undefined}
      style={{
        display: "flex", flexDirection: "column",
        width: isCollapsed ? "var(--nav-width-collapsed)" : "var(--nav-width)",
        flex: "0 0 auto", height: "100%", overflow: "hidden",
        background: "var(--surface-nav)",
        backdropFilter: "var(--glass-film-strong)",
        WebkitBackdropFilter: "var(--glass-film-strong)",
        borderRight: "var(--border-width) solid var(--glass-edge)",
        color: "var(--text-on-nav)",
        transition: "width var(--duration-slow) var(--ease-standard)",
        ...style,
      }}
      {...rest}
    >
      <div
        style={{
          display: "flex", alignItems: "center", gap: "var(--space-2)",
          height: "var(--header-height)", flex: "0 0 auto",
          padding: isCollapsed ? "0 var(--space-2)" : "0 var(--space-3) 0 var(--space-4)",
          justifyContent: isCollapsed ? "center" : "flex-start",
          borderBottom: "var(--border-width) solid var(--border-nav)",
        }}
      >
        <BrandMark brand={brand} logo={logo} logoCollapsed={logoCollapsed} collapsed={isCollapsed} collapsible={collapsible} />
        {collapsible ? (
          <RailButton
            label={isCollapsed ? "Expand navigation" : "Collapse navigation"}
            icon={isCollapsed ? "panel-left-open" : "panel-left-close"}
            onClick={() => setCollapsed(!isCollapsed)}
            expanded={!isCollapsed}
          />
        ) : null}
      </div>

      <div style={{ flex: 1, overflowY: "auto", overflowX: "hidden", padding: "var(--space-3) 0" }}>
        {sections.map((section, i) => (
          <div key={section.id} style={{ marginBottom: "var(--space-1)" }}>
            {section.label && !isCollapsed ? (
              <button
                type="button"
                onClick={() => toggleSection(section.id)}
                aria-expanded={open[section.id] !== false}
                style={{
                  display: "flex", alignItems: "center", gap: "var(--space-2)", width: "100%",
                  padding: "6px var(--space-4)", background: "transparent", border: 0,
                  color: "var(--text-muted)", fontSize: "var(--text-2xs)",
                  fontWeight: "var(--weight-semibold)", letterSpacing: "var(--tracking-caps)",
                  textTransform: "uppercase", cursor: "pointer", textAlign: "left",
                }}
              >
                <Icon name={open[section.id] !== false ? "chevron-down" : "chevron-right"} size={12} strokeWidth={2.25} />
                <span style={{ flex: 1 }}>{section.label}</span>
              </button>
            ) : null}
            {/* Collapsed, a section's label can't show — a hairline keeps the grouping. */}
            {isCollapsed && i > 0 ? (
              <div style={{ height: 1, margin: "var(--space-2) var(--space-3)", background: "var(--border-nav)" }} />
            ) : null}
            {open[section.id] !== false || isCollapsed ? (
              <NavTree
                items={section.items || []}
                depth={0}
                activeId={activeId}
                collapsed={isCollapsed}
                onNavigate={onNavigate}
                onExpandRail={() => setCollapsed(false)}
              />
            ) : null}
          </div>
        ))}
      </div>

      {footer ? (
        <div
          style={{
            flex: "0 0 auto", padding: isCollapsed ? "var(--space-3) var(--space-2)" : "var(--space-3) var(--space-4)",
            borderTop: "var(--border-width) solid var(--border-nav)",
            display: "flex", justifyContent: isCollapsed ? "center" : "flex-start", overflow: "hidden",
          }}
        >
          {typeof footer === "function" ? footer({ collapsed: isCollapsed }) : footer}
        </div>
      ) : null}
    </nav>
  );
}

/* True when this item or anything under it is the active route — used to open the group it
   sits in and to mark that group's parent row. */
function containsActive(item, activeId) {
  if (!activeId) return false;
  if (item.id === activeId) return true;
  return (item.items || []).some((child) => containsActive(child, activeId));
}

function NavTree({ items, depth, activeId, collapsed, onNavigate, onExpandRail }) {
  return items.map((item) =>
    (item.items || []).length ? (
      <NavGroup
        key={item.id}
        item={item}
        depth={depth}
        activeId={activeId}
        collapsed={collapsed}
        onNavigate={onNavigate}
        onExpandRail={onExpandRail}
      />
    ) : (
      <NavItem
        key={item.id}
        item={item}
        depth={depth}
        active={item.id === activeId}
        collapsed={collapsed}
        onNavigate={onNavigate}
      />
    )
  );
}

function NavGroup({ item, depth, activeId, collapsed, onNavigate, onExpandRail }) {
  const holdsActive = containsActive(item, activeId);
  const [open, setOpen] = React.useState(holdsActive || item.defaultOpen === true);
  const [hover, setHover] = React.useState(false);
  React.useEffect(() => { if (holdsActive) setOpen(true); }, [holdsActive]);

  /* At 56px there is no room for a submenu, so the disclosure becomes "give me the rail back". */
  const onClick = () => (collapsed ? (onExpandRail(), setOpen(true)) : setOpen((o) => !o));

  return (
    <React.Fragment>
      <button
        type="button"
        onClick={onClick}
        aria-expanded={collapsed ? undefined : open}
        title={collapsed ? item.label : undefined}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        style={{
          ...rowStyle(depth, collapsed),
          /* Matches the link rows: they reserve 3px on the left for the active marker, so
             without it the group's icon sits 3px further left than its children. */
          borderLeft: "var(--border-accent-width) solid transparent",
          paddingLeft: collapsed ? 0 : `calc(${indent(depth)} - var(--border-accent-width))`,
          borderTop: 0, borderRight: 0, borderBottom: 0,
          cursor: "pointer", textAlign: "left",
          color: holdsActive ? "var(--text-on-nav-active)" : "var(--text-on-nav)",
          background: hover ? "var(--surface-nav-hover)" : "transparent",
          fontWeight: holdsActive ? "var(--weight-medium)" : "var(--weight-regular)",
        }}
      >
        {item.icon ? <IconSlot name={item.icon} depth={depth} /> : null}
        {!collapsed ? (
          <React.Fragment>
            <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{item.label}</span>
            <Icon name={open ? "chevron-down" : "chevron-right"} size={13} strokeWidth={2.25} color="var(--text-subtle)" />
          </React.Fragment>
        ) : null}
      </button>
      {open && !collapsed ? (
        <NavTree
          items={item.items}
          depth={depth + 1}
          activeId={activeId}
          collapsed={collapsed}
          onNavigate={onNavigate}
          onExpandRail={onExpandRail}
        />
      ) : null}
    </React.Fragment>
  );
}

function NavItem({ item, depth, active, collapsed, onNavigate }) {
  const [hover, setHover] = React.useState(false);
  return (
    <a
      href={item.href || "#"}
      aria-current={active ? "page" : undefined}
      title={collapsed ? item.label : undefined}
      onClick={(e) => { e.preventDefault(); onNavigate && onNavigate(item.id); }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        ...rowStyle(depth, collapsed),
        textDecoration: "none",
        borderLeft: `var(--border-accent-width) solid ${active ? "var(--crimson-500)" : "transparent"}`,
        paddingLeft: collapsed ? 0 : `calc(${indent(depth)} - var(--border-accent-width))`,
        color: active ? "var(--text-on-nav-active)" : "var(--text-on-nav)",
        background: active ? "var(--surface-nav-active)" : hover ? "var(--surface-nav-hover)" : "transparent",
        boxShadow: active ? "var(--shadow-nav-active)" : "none",
        fontWeight: active ? "var(--weight-medium)" : "var(--weight-regular)",
        fontSize: depth > 0 ? "var(--text-sm)" : "var(--text-base)",
      }}
    >
      {item.icon ? <IconSlot name={item.icon} depth={depth} /> : null}
      {!collapsed ? <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{item.label}</span> : null}
      {!collapsed && item.count != null ? (
        <span
          style={{
            fontSize: "var(--text-2xs)", fontWeight: "var(--weight-semibold)",
            fontVariantNumeric: "tabular-nums",
            padding: "1px 5px", borderRadius: "var(--radius-sm)",
            background: item.countTone === "danger" ? "var(--crimson-500)" : "var(--neutral-tint)",
            color: item.countTone === "danger" ? "#fff" : "var(--neutral-ink)",
          }}
        >
          {item.count}
        </span>
      ) : null}
      {/* Collapsed, a count would not fit — it becomes a dot on the icon's corner. */}
      {collapsed && item.count != null ? (
        <span
          style={{
            position: "absolute", marginLeft: 14, marginTop: -12,
            width: 6, height: 6, borderRadius: "50%",
            background: item.countTone === "danger" ? "var(--crimson-500)" : "var(--navy-400)",
          }}
        />
      ) : null}
      {!collapsed && item.external ? <Icon name="external-link" size={12} color="var(--text-subtle)" /> : null}
    </a>
  );
}

/* Every rail icon sits in a fixed 16px centred box. Lucide glyphs do not share a common ink
   box — a full-bleed circle like `globe` runs edge to edge where `cable` is inset — so
   dropping them straight into the flex row makes single icons look off-axis. The slot pins
   the axis regardless of which glyph is used. */
function IconSlot({ name, depth }) {
  return (
    <span style={{ width: 16, flex: "0 0 16px", display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
      <Icon name={name} size={depth === 0 ? 16 : 14} />
    </span>
  );
}

/* Each level steps in 14px. Deeper rows are also a size smaller, so the hierarchy reads
   without needing connector lines. */
const indent = (depth) => `calc(var(--space-3) + ${depth * 14}px)`;

const rowStyle = (depth, collapsed) => ({
  display: "flex", alignItems: "center", gap: "var(--space-3)",
  height: depth > 0 ? 31 : 34,
  margin: "2px var(--space-2)",
  padding: collapsed ? 0 : `0 var(--space-3) 0 ${indent(depth)}`,
  justifyContent: collapsed ? "center" : "flex-start",
  borderRadius: "var(--radius-nav-item)",
  fontSize: depth > 0 ? "var(--text-sm)" : "var(--text-base)",
  transition: "var(--transition-control)",
});

/* The brand slot: a fixed 28px-tall box, so swapping the wordmark for an image does not
   shift the rail. `logo` takes an image URL or your own node; `logoCollapsed` is the mark
   used at 56px. With no logo it sets `brand` in type — no logo file was supplied with this
   system. Collapsed, the slot yields to the collapse toggle: 56px does not fit both. */
function BrandMark({ brand, logo, logoCollapsed, collapsed, collapsible }) {
  if (collapsed && collapsible) return null;
  const src = collapsed ? logoCollapsed || logo : logo;
  if (!src) {
    return collapsed ? (
      <span style={{ fontSize: "var(--text-md)", fontWeight: "var(--weight-bold)", color: "var(--text-heading)" }}>{brand.slice(0, 1)}</span>
    ) : (
      <span style={{ flex: 1, minWidth: 0, height: 28, display: "flex", alignItems: "center", fontSize: "var(--text-lg)", fontWeight: "var(--weight-bold)", letterSpacing: "-0.02em", color: "var(--text-heading)", whiteSpace: "nowrap", overflow: "hidden" }}>
        {brand}
      </span>
    );
  }
  return (
    <span style={{ flex: collapsed ? "0 0 auto" : 1, minWidth: 0, height: 28, display: "flex", alignItems: "center" }}>
      {typeof src === "string" ? (
        <img src={src} alt={brand} style={{ height: "100%", width: "auto", maxWidth: "100%", objectFit: "contain", objectPosition: "left center", display: "block" }} />
      ) : src}
    </span>
  );
}

function RailButton({ label, icon, onClick, expanded }) {
  const [hover, setHover] = React.useState(false);
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      aria-expanded={expanded}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        width: 28, height: 28, flex: "0 0 auto", padding: 0, cursor: "pointer",
        borderRadius: "var(--radius-control)", border: 0,
        background: hover ? "var(--action-ghost-bg-hover)" : "transparent",
        color: "var(--text-muted)", transition: "var(--transition-control)",
      }}
    >
      <Icon name={icon} size={16} />
    </button>
  );
}
