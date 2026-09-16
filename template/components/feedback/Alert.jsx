import React from "react";
import { Icon } from "../core/Icon.jsx";
import { IconButton } from "../core/IconButton.jsx";

const TONES = {
  info: ["var(--info-tint)", "var(--info-ink)", "var(--info-edge)", "info"],
  success: ["var(--success-tint)", "var(--success-ink)", "var(--success-edge)", "circle-check"],
  warning: ["var(--warning-tint)", "var(--warning-ink)", "var(--warning-edge)", "triangle-alert"],
  danger: ["var(--danger-tint)", "var(--danger-ink)", "var(--danger-edge)", "octagon-alert"],
};

/* Page-level message: validation summary after a failed POST, maintenance notice,
   "this record is locked pending review". Tinted, 1px border, no shadow. */
export function Alert({ tone = "info", title, children, action, onDismiss, icon, style, ...rest }) {
  const [tint, ink, edge, defaultIcon] = TONES[tone] || TONES.info;
  return (
    <div
      role={tone === "danger" ? "alert" : "status"}
      style={{
        display: "flex", alignItems: "flex-start", gap: "var(--space-3)",
        padding: "var(--space-3) var(--space-4)",
        background: tint, border: `var(--border-width) solid ${edge}`,
        borderRadius: "var(--radius-md)", minWidth: 0,
        ...style,
      }}
      {...rest}
    >
      <span style={{ color: ink, marginTop: 1, flex: "0 0 auto" }}>
        <Icon name={icon || defaultIcon} size={16} />
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        {title ? (
          <div style={{ fontSize: "var(--text-base)", fontWeight: "var(--weight-semibold)", color: ink, marginBottom: children ? 2 : 0 }}>{title}</div>
        ) : null}
        {children ? <div style={{ fontSize: "var(--text-sm)", color: "var(--text-body)", textWrap: "pretty" }}>{children}</div> : null}
        {action ? <div style={{ marginTop: "var(--space-2)" }}>{action}</div> : null}
      </div>
      {onDismiss ? <IconButton icon="x" label="Dismiss" size="sm" onClick={onDismiss} /> : null}
    </div>
  );
}
