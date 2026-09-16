import React from "react";
import { Icon } from "../core/Icon.jsx";

export function EmptyState({ icon = "inbox", title, description, action, compact = false, style, ...rest }) {
  return (
    <div
      style={{
        display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
        gap: "var(--space-2)", textAlign: "center",
        padding: compact ? "var(--space-6) var(--space-4)" : "var(--space-12) var(--space-6)",
        ...style,
      }}
      {...rest}
    >
      <span
        style={{
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          width: 40, height: 40, marginBottom: "var(--space-1)",
          background: "var(--surface-sunken)",
          border: "var(--border-width) solid var(--border-subtle)",
          borderRadius: "var(--radius-lg)",
          color: "var(--text-subtle)",
        }}
      >
        <Icon name={icon} size={18} />
      </span>
      {title ? (
        <h4 style={{ margin: 0, fontSize: "var(--text-base)", fontWeight: "var(--weight-semibold)", color: "var(--text-heading)" }}>{title}</h4>
      ) : null}
      {description ? (
        <p style={{ margin: 0, maxWidth: 380, fontSize: "var(--text-sm)", color: "var(--text-muted)", textWrap: "pretty" }}>{description}</p>
      ) : null}
      {action ? <div style={{ marginTop: "var(--space-2)" }}>{action}</div> : null}
    </div>
  );
}
