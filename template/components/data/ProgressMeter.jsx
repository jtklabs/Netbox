import React from "react";

const TONES = {
  brand: "var(--navy-500)",
  success: "var(--success-ink)",
  warning: "var(--warning-ink)",
  danger: "var(--danger-ink)",
};

/* Horizontal completion bar — compliance coverage, evidence collected, upload progress.
   4px track, square ends, no animation on load. */
export function ProgressMeter({ value = 0, max = 100, label, valueText, tone = "brand", size = "md", style, ...rest }) {
  const pct = Math.max(0, Math.min(100, (value / (max || 1)) * 100));
  const h = size === "sm" ? 4 : size === "lg" ? 8 : 6;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)", minWidth: 0, ...style }} {...rest}>
      {label || valueText ? (
        <div style={{ display: "flex", alignItems: "baseline", gap: "var(--space-2)", fontSize: "var(--text-xs)" }}>
          {label ? <span style={{ flex: 1, minWidth: 0, color: "var(--text-body)" }}>{label}</span> : null}
          {valueText ? (
            <span style={{ color: "var(--text-muted)", fontVariantNumeric: "tabular-nums", fontWeight: "var(--weight-medium)" }}>{valueText}</span>
          ) : null}
        </div>
      ) : null}
      <div
        role="progressbar"
        aria-valuenow={value}
        aria-valuemin={0}
        aria-valuemax={max}
        style={{ height: h, width: "100%", background: "var(--gray-100)", borderRadius: "var(--radius-sm)", overflow: "hidden" }}
      >
        <div style={{ height: "100%", width: pct + "%", background: TONES[tone] || TONES.brand, borderRadius: "var(--radius-sm)" }} />
      </div>
    </div>
  );
}
