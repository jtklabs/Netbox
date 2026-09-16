const { SidebarNav, Breadcrumbs, Icon, IconButton, Input, Button, Tooltip, Tabs } = window.PortalDesignSystem_986bf0;

/* The authenticated chrome: collapsible left rail, frosted top bar, scrolling content column.
   The rail remembers its collapsed state itself — nothing here needs to hold it. */
function AppShell({ activeId, onNavigate, theme, onToggleTheme, breadcrumbs, title, subtitle, actions, tabs, activeTab, onTabChange, children, toolbar }) {
  /* Transparent: the lit field is fixed on <body> and must show through the chrome. */
  return (
    <div style={{ display: "flex", height: "100%", minHeight: 0, background: "transparent" }}>
      <SidebarNav
        brand="Nova"
        sections={window.NovaData.navSections}
        activeId={activeId}
        onNavigate={onNavigate}
        footer={({ collapsed }) => (
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", width: "100%", minWidth: 0 }}>
            <span
              style={{
                display: "inline-flex", alignItems: "center", justifyContent: "center",
                width: 26, height: 26, flex: "0 0 auto",
                background: "var(--navy-700)", borderRadius: "var(--radius-pill)",
                fontSize: "var(--text-2xs)", fontWeight: 600, color: "#fff",
              }}
            >
              DO
            </span>
            {/* At 56px only the avatar fits; the name and the theme toggle drop out. */}
            {collapsed ? null : (
              <span style={{ flex: 1, minWidth: 0, fontSize: "var(--text-xs)", color: "var(--text-body)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                D. Okafor
              </span>
            )}
            {collapsed ? null : (
            <Tooltip label={theme === "dark" ? "Switch to light" : "Switch to dark"} placement="top">
              <button
                type="button"
                onClick={onToggleTheme}
                aria-label="Toggle theme"
                style={{
                  display: "inline-flex", alignItems: "center", justifyContent: "center",
                  width: 26, height: 26, background: "transparent", color: "var(--text-muted)",
                  border: "1px solid var(--border-input)", borderRadius: "var(--radius-control)", cursor: "pointer",
                }}
              >
                <Icon name={theme === "dark" ? "sun" : "moon"} size={13} />
              </button>
            </Tooltip>
            )}
          </div>
        )}
      />

      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", position: "relative", zIndex: 0 }}>
        <header
          style={{
            display: "flex", alignItems: "center", gap: "var(--space-4)", flex: "0 0 auto",
            height: "var(--header-height)", padding: "0 var(--page-gutter)",
            background: "var(--surface-header)",
            backdropFilter: "var(--glass-film-strong)",
            WebkitBackdropFilter: "var(--glass-film-strong)",
            borderBottom: "var(--border-width) solid var(--glass-edge)",
          }}
        >
          <div style={{ width: 300, maxWidth: "40%" }}>
            <Input size="sm" iconLeft="search" placeholder="Search assets, reports, tasks" />
          </div>
          <div style={{ flex: 1 }} />
          <Button variant="ghost" size="sm" iconLeft="circle-help">Help</Button>
          <Tooltip label="3 overdue reports" placement="bottom">
            <span style={{ position: "relative", display: "inline-flex" }}>
              <IconButton icon="bell" label="Notifications" />
              <span style={{ position: "absolute", top: 5, right: 5, width: 7, height: 7, borderRadius: "var(--radius-pill)", background: "var(--crimson-500)", border: "1.5px solid var(--surface-header)" }} />
            </span>
          </Tooltip>
        </header>

        <div style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
          {/* Fluid, not capped: collapsing the rail has to give the table the width back. */}
          <div style={{ width: "100%", padding: "var(--page-gutter)", display: "flex", flexDirection: "column", gap: "var(--gap-section)" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
              {breadcrumbs ? <Breadcrumbs items={breadcrumbs} onNavigate={onNavigate} /> : null}
              <div style={{ display: "flex", alignItems: "flex-end", gap: "var(--space-4)", flexWrap: "wrap" }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <h1 style={{ fontSize: "var(--type-page-title-size)", fontWeight: "var(--type-page-title-weight)", letterSpacing: "var(--type-page-title-tracking)" }}>{title}</h1>
                  {subtitle ? <p style={{ margin: "4px 0 0", fontSize: "var(--text-sm)", color: "var(--text-muted)" }}>{subtitle}</p> : null}
                </div>
                {actions ? <div style={{ display: "flex", alignItems: "center", gap: "var(--gap-inline)" }}>{actions}</div> : null}
              </div>
              {tabs ? <Tabs tabs={tabs} activeId={activeTab} onChange={onTabChange} /> : null}
              {toolbar || null}
            </div>
            {children}
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { AppShell });
