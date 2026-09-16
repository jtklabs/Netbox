import React from "react";
import { Icon } from "../core/Icon.jsx";
import { IconButton } from "../core/IconButton.jsx";

const TONES = {
  success: ["var(--success-ink)", "circle-check"],
  info: ["var(--info-ink)", "info"],
  warning: ["var(--warning-ink)", "triangle-alert"],
  danger: ["var(--danger-ink)", "octagon-alert"],
};

/* Confirmation of something that already happened ("Task reassigned", "Export queued").
   Bottom-right, one at a time, auto-dismiss. Never use one to report a validation error —
   that belongs in an Alert at the top of the form. */
export function Toast({ tone = "success", title, children, onDismiss, action, style, ...rest }) {
  const [ink, icon] = TONES[tone] || TONES.success;
  return (
    <div
      role="status"
      style={{
        display: "flex", alignItems: "flex-start", gap: "var(--space-3)",
        width: 340, padding: "var(--space-3)",
        background: "var(--surface-raised)",
        backdropFilter: "var(--glass-film-strong)",
        WebkitBackdropFilter: "var(--glass-film-strong)",
        border: "var(--border-width) solid var(--glass-edge-strong)",
        borderRadius: "var(--radius-md)",
        boxShadow: "var(--shadow-lg)",
        animation: "ds-toast-in var(--duration-base) var(--ease-out)",
        ...style,
      }}
      {...rest}
    >
      <span style={{ color: ink, marginTop: 1, flex: "0 0 auto" }}><Icon name={icon} size={16} /></span>
      <div style={{ flex: 1, minWidth: 0 }}>
        {title ? <div style={{ fontSize: "var(--text-base)", fontWeight: "var(--weight-medium)", color: "var(--text-strong)" }}>{title}</div> : null}
        {children ? <div style={{ marginTop: 2, fontSize: "var(--text-sm)", color: "var(--text-muted)" }}>{children}</div> : null}
        {action ? <div style={{ marginTop: "var(--space-2)" }}>{action}</div> : null}
      </div>
      {onDismiss ? <IconButton icon="x" label="Dismiss" size="sm" onClick={onDismiss} /> : null}
    </div>
  );
}
