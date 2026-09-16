import React from "react";

const STATES = {
  active:      ["success", "Active"],
  compliant:   ["success", "Compliant"],
  passed:      ["success", "Passed"],
  approved:    ["success", "Approved"],
  submitted:   ["info", "Submitted"],
  inreview:    ["info", "In review"],
  draft:       ["neutral", "Draft"],
  planned:     ["neutral", "Planned"],
  decommissioned: ["neutral", "Decommissioned"],
  duesoon:     ["warning", "Due soon"],
  pending:     ["warning", "Pending"],
  exception:   ["warning", "Exception"],
  overdue:     ["danger", "Overdue"],
  failed:      ["danger", "Failed"],
  noncompliant:["danger", "Non-compliant"],
};

const TONES = {
  neutral: ["var(--neutral-tint)", "var(--neutral-ink)", "var(--neutral-edge)"],
  info: ["var(--info-tint)", "var(--info-ink)", "var(--info-edge)"],
  success: ["var(--success-tint)", "var(--success-ink)", "var(--success-edge)"],
  warning: ["var(--warning-tint)", "var(--warning-ink)", "var(--warning-edge)"],
  danger: ["var(--danger-tint)", "var(--danger-ink)", "var(--danger-edge)"],
};

/* Lifecycle state in tables and record headers. Pill-shaped with a solid dot,
   so it never reads as a Badge (square) or a Tag (removable). */
export function StatusPill({ status, tone, children, style, ...rest }) {
  const key = String(status || "").toLowerCase().replace(/[\s-_]/g, "");
  const [mappedTone, mappedLabel] = STATES[key] || ["neutral", status];
  const t = tone || mappedTone;
  const [tint, ink, edge] = TONES[t] || TONES.neutral;
  return (
    <span
      style={{
        display: "inline-flex", alignItems: "center", gap: 5,
        height: 22, padding: "0 9px 0 7px",
        fontSize: "var(--text-xs)", fontWeight: "var(--weight-medium)", lineHeight: 1,
        color: ink, background: tint,
        border: `var(--border-width) solid ${edge}`,
        borderRadius: "var(--radius-status)", whiteSpace: "nowrap",
        ...style,
      }}
      {...rest}
    >
      <span aria-hidden="true" style={{ width: 6, height: 6, borderRadius: "var(--radius-pill)", background: ink, flex: "0 0 auto" }} />
      {children || mappedLabel}
    </span>
  );
}
