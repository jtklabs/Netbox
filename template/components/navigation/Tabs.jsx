import React from "react";
import { Icon } from "../core/Icon.jsx";

/* Underline tabs for sub-views of one record (Overview / Config / Compliance / History). */
export function Tabs({ tabs = [], activeId, onChange, style, ...rest }) {
  return (
    <div
      role="tablist"
      style={{
        display: "flex", alignItems: "stretch", gap: "var(--space-5)",
        borderBottom: "var(--border-width) solid var(--border-default)",
        minWidth: 0, overflowX: "auto",
        ...style,
      }}
      {...rest}
    >
      {tabs.map((t) => (
        <Tab key={t.id} tab={t} active={t.id === activeId} onChange={onChange} />
      ))}
    </div>
  );
}

function Tab({ tab, active, onChange }) {
  const [hover, setHover] = React.useState(false);
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      disabled={tab.disabled}
      onClick={() => onChange && onChange(tab.id)}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "inline-flex", alignItems: "center", gap: "var(--space-15)",
        padding: "0 0 9px", marginBottom: -1, background: "transparent",
        border: 0, borderBottom: `var(--border-width-thick) solid ${active ? "var(--navy-700)" : "transparent"}`,
        color: tab.disabled ? "var(--text-subtle)" : active ? "var(--text-heading)" : hover ? "var(--text-body)" : "var(--text-muted)",
        fontSize: "var(--text-base)", fontWeight: active ? "var(--weight-semibold)" : "var(--weight-medium)",
        whiteSpace: "nowrap", cursor: tab.disabled ? "not-allowed" : "pointer",
        transition: "var(--transition-control)", paddingTop: 9,
      }}
    >
      {tab.icon ? <Icon name={tab.icon} size={14} /> : null}
      {tab.label}
      {tab.count != null ? (
        <span style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)", fontVariantNumeric: "tabular-nums" }}>{tab.count}</span>
      ) : null}
    </button>
  );
}
