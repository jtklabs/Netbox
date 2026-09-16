import React from "react";

const TONES = {
  neutral: ["var(--neutral-tint)", "var(--neutral-ink)", "var(--neutral-edge)"],
  info: ["var(--info-tint)", "var(--info-ink)", "var(--info-edge)"],
  success: ["var(--success-tint)", "var(--success-ink)", "var(--success-edge)"],
  warning: ["var(--warning-tint)", "var(--warning-ink)", "var(--warning-edge)"],
  danger: ["var(--danger-tint)", "var(--danger-ink)", "var(--danger-edge)"],
  brand: ["var(--navy-50)", "var(--navy-600)", "var(--navy-100)"],
};

/* Square-cornered count/label chip. For lifecycle state use StatusPill instead. */
export function Badge({ children, tone = "neutral", solid = false, style, ...rest }) {
  const [tint, ink, edge] = TONES[tone] || TONES.neutral;
  return (
    <span
      style={{
        display: "inline-flex", alignItems: "center", gap: "var(--space-1)",
        height: 20, padding: "0 6px",
        fontSize: "var(--text-2xs)", fontWeight: "var(--weight-semibold)",
        letterSpacing: "var(--tracking-wide)", lineHeight: 1, whiteSpace: "nowrap",
        color: solid ? "var(--text-inverse)" : ink,
        background: solid ? ink : tint,
        border: `var(--border-width) solid ${solid ? "transparent" : edge}`,
        borderRadius: "var(--radius-sm)",
        fontVariantNumeric: "tabular-nums",
        ...style,
      }}
      {...rest}
    >
      {children}
    </span>
  );
}
