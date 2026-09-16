import React from "react";

export function Radio({ label, description, checked, disabled = false, onChange, name, value, id, style, ...rest }) {
  const [focus, setFocus] = React.useState(false);
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
          id={id} type="radio" name={name} value={value} checked={!!checked} disabled={disabled} onChange={onChange}
          onFocus={() => setFocus(true)} onBlur={() => setFocus(false)}
          style={{ position: "absolute", opacity: 0, width: 16, height: 16, margin: 0, cursor: "inherit" }}
          {...rest}
        />
        <span
          aria-hidden="true"
          style={{
            display: "inline-flex", alignItems: "center", justifyContent: "center",
            width: 16, height: 16, borderRadius: "var(--radius-pill)",
            background: disabled ? "var(--surface-disabled)" : "var(--surface-input)",
            border: `${checked && !disabled ? "5px" : "1px"} solid ${checked && !disabled ? "var(--navy-700)" : "var(--border-input)"}`,
            boxShadow: focus ? "var(--focus-ring)" : "none",
            transition: "var(--transition-control)",
          }}
        />
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
