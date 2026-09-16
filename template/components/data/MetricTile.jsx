import React from "react";
import { Icon } from "../core/Icon.jsx";

/* Single number on the dashboard. Bordered like a Card but its own component
   because the metric type role and delta line are fixed.

   The delta has TWO independent properties, because half the numbers in this portal are
   better when they fall: `deltaTone` sets the colour (is this good or bad news) and
   `deltaDirection` sets the arrow (which way did it move). A non-compliant count dropping by
   44 is deltaTone="positive" deltaDirection="down" — green text, downward arrow. The legacy
   "up"/"down" tone values still work and drive both, so existing tiles are unaffected. */
export function MetricTile({
  label, value, unit, delta, deltaTone = "neutral", deltaDirection, icon, footnote, onClick, style, ...rest
}) {
  const [hover, setHover] = React.useState(false);
  const positive = deltaTone === "positive" || deltaTone === "up";
  const negative = deltaTone === "negative" || deltaTone === "down";
  const deltaColor = positive ? "var(--success-ink)" : negative ? "var(--danger-ink)" : "var(--text-muted)";
  /* No explicit direction: fall back to the legacy reading where the tone was the arrow. */
  const direction = deltaDirection || (deltaTone === "up" ? "up" : deltaTone === "down" ? "down" : "none");
  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "flex", flexDirection: "column", gap: "var(--space-1)", minWidth: 0,
        padding: "var(--pad-card)",
        background: "var(--surface-card)",
        backdropFilter: "var(--glass-film)",
        WebkitBackdropFilter: "var(--glass-film)",
        border: "var(--border-width) solid " + (hover && onClick ? "var(--border-strong)" : "var(--glass-edge)"),
        borderRadius: "var(--radius-card)",
        cursor: onClick ? "pointer" : "default",
        transition: "var(--transition-control)",
        ...style,
      }}
      {...rest}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
        <span
          style={{
            flex: 1, minWidth: 0,
            fontSize: "var(--type-eyebrow-size)", letterSpacing: "var(--type-eyebrow-tracking)",
            textTransform: "uppercase", fontWeight: "var(--weight-semibold)", color: "var(--text-muted)",
          }}
        >
          {label}
        </span>
        {icon ? <Icon name={icon} size={15} color="var(--text-subtle)" /> : null}
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: "var(--space-15)" }}>
        <span
          style={{
            fontSize: "var(--type-metric-size)", fontWeight: "var(--type-metric-weight)",
            letterSpacing: "var(--tracking-tight)", color: "var(--text-heading)",
            fontVariantNumeric: "tabular-nums", lineHeight: 1.1,
          }}
        >
          {value}
        </span>
        {unit ? <span style={{ fontSize: "var(--text-sm)", color: "var(--text-muted)" }}>{unit}</span> : null}
      </div>
      {delta || footnote ? (
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", fontSize: "var(--text-xs)" }}>
          {delta ? (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 3, color: deltaColor, fontWeight: "var(--weight-medium)" }}>
              {direction === "up" ? <Icon name="trending-up" size={12} /> : direction === "down" ? <Icon name="trending-down" size={12} /> : null}
              {delta}
            </span>
          ) : null}
          {footnote ? <span style={{ color: "var(--text-muted)" }}>{footnote}</span> : null}
        </div>
      ) : null}
    </div>
  );
}
