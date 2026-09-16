import React from "react";

/* The portal's only container. A frosted panel: 8px radius, light rim, soft spread.
   Use `title` + `actions` for the standard header rule; `padding="none"` for tables. */
export function Card({ title, subtitle, actions, footer, padding = "md", children, style, ...rest }) {
  const pad = padding === "none" ? 0 : padding === "lg" ? "var(--pad-card-lg)" : "var(--pad-card)";
  return (
    <section
      style={{
        display: "flex", flexDirection: "column", minWidth: 0,
        background: "var(--surface-card)",
        backdropFilter: "var(--glass-film)",
        WebkitBackdropFilter: "var(--glass-film)",
        border: "var(--border-width) solid var(--glass-edge)",
        borderRadius: "var(--radius-card)",
        boxShadow: "var(--shadow-card)",
        ...style,
      }}
      {...rest}
    >
      {title || actions ? (
        <header
          style={{
            display: "flex", alignItems: "center", gap: "var(--space-3)",
            padding: "var(--space-3) var(--pad-card)",
            borderBottom: "var(--border-width) solid var(--border-subtle)",
            minHeight: 44,
          }}
        >
          <div style={{ minWidth: 0, flex: 1 }}>
            {title ? (
              <h3 style={{ margin: 0, fontSize: "var(--type-card-title-size)", fontWeight: "var(--type-card-title-weight)", color: "var(--text-heading)", letterSpacing: 0 }}>{title}</h3>
            ) : null}
            {subtitle ? (
              <p style={{ margin: "2px 0 0", fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>{subtitle}</p>
            ) : null}
          </div>
          {actions ? <div style={{ display: "flex", alignItems: "center", gap: "var(--gap-inline)" }}>{actions}</div> : null}
        </header>
      ) : null}
      <div style={{ padding: pad, minWidth: 0, flex: 1 }}>{children}</div>
      {footer ? (
        <footer
          style={{
            display: "flex", alignItems: "center", justifyContent: "flex-end", gap: "var(--gap-inline)",
            padding: "var(--space-3) var(--pad-card)",
            borderTop: "var(--border-width) solid var(--border-subtle)",
            background: "var(--surface-sunken)",
            borderRadius: "0 0 var(--radius-card) var(--radius-card)",
          }}
        >
          {footer}
        </footer>
      ) : null}
    </section>
  );
}
