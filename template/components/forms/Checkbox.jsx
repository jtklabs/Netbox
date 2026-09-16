import React from "react";

export function Checkbox({ label, description, checked, indeterminate = false, disabled = false, onChange, id, style, ...rest }) {
  const [focus, setFocus] = React.useState(false);
  const ref = React.useRef(null);
  React.useEffect(() => { if (ref.current) ref.current.indeterminate = indeterminate; }, [indeterminate]);
  const on = checked || indeterminate;
  return (
    <label
      style={{
        display: "inline-flex", alignItems: "flex-start", gap: "var(--space-2)",
        cursor: disabled ? "not-allowed" : "pointer", minWidth: 0,
        color: disabled ? "var(--text-subtle)" : "var(--text-body)",
        ...style,
      }}
    >
      <span style={{ position: "relative", display: "inline-flex", flex: "0 0 auto", marginTop: 1 }}>
        <input
          ref={ref} id={id} type="checkbox" checked={!!checked} disabled={disabled} onChange={onChange}
          onFocus={() => setFocus(true)} onBlur={() => setFocus(false)}
          style={{ position: "absolute", opacity: 0, width: 16, height: 16, margin: 0, cursor: "inherit" }}
          {...rest}
        />
        <span
          aria-hidden="true"
          style={{
            display: "inline-flex", alignItems: "center", justifyContent: "center",
            width: 16, height: 16,
            background: disabled ? "var(--surface-disabled)" : on ? "var(--navy-700)" : "var(--surface-input)",
            border: `var(--border-width) solid ${on && !disabled ? "var(--navy-700)" : "var(--border-input)"}`,
            borderRadius: "var(--radius-sm)",
            boxShadow: focus ? "var(--focus-ring)" : "none",
            transition: "var(--transition-control)",
          }}
        >
          {indeterminate ? (
            <svg width="10" height="10" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14" stroke="#fff" strokeWidth="3.5" strokeLinecap="round" /></svg>
          ) : checked ? (
            <svg width="11" height="11" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12.5l5 5L20 6.5" fill="none" stroke="#fff" strokeWidth="3.25" strokeLinecap="round" strokeLinejoin="round" /></svg>
          ) : null}
        </span>
      </span>
      {label || description ? (
        <span style={{ minWidth: 0 }}>
          <span style={{ display: "block", fontSize: "var(--text-base)", lineHeight: 1.35 }}>{label}</span>
          {description ? (
            <span style={{ display: "block", marginTop: 2, fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>{description}</span>
          ) : null}
        </span>
      ) : null}
    </label>
  );
}
