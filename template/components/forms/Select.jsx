import React from "react";

/* Native select with the portal's control chrome and a drawn chevron. */
export function Select({ options = [], placeholder, size = "md", invalid = false, disabled = false, style, ...rest }) {
  const [focus, setFocus] = React.useState(false);
  const h = size === "sm" ? "var(--control-height-sm)" : size === "lg" ? "var(--control-height-lg)" : "var(--control-height)";
  const border = invalid ? "var(--border-danger)" : focus ? "var(--border-focus)" : "var(--border-input)";
  return (
    <div style={{ position: "relative", minWidth: 0, ...style }}>
      <select
        disabled={disabled}
        aria-invalid={invalid || undefined}
        onFocus={() => setFocus(true)}
        onBlur={() => setFocus(false)}
        style={{
          width: "100%", height: h,
          padding: "0 30px 0 var(--pad-control-x)",
          fontFamily: "var(--font-sans)",
          fontSize: size === "sm" ? "var(--text-sm)" : "var(--text-base)",
          color: disabled ? "var(--text-subtle)" : "var(--text-body)",
          background: disabled ? "var(--surface-disabled)" : "var(--surface-input)",
          backdropFilter: "var(--glass-film-subtle)",
          WebkitBackdropFilter: "var(--glass-film-subtle)",
          border: `var(--border-width) solid ${border}`,
          borderRadius: "var(--radius-control)",
          boxShadow: focus ? (invalid ? "var(--focus-ring-danger)" : "var(--focus-ring)") : "none",
          appearance: "none", WebkitAppearance: "none", outline: "none",
          cursor: disabled ? "not-allowed" : "pointer",
          transition: "var(--transition-control)",
        }}
        {...rest}
      >
        {placeholder ? <option value="">{placeholder}</option> : null}
        {options.map((o) => {
          const value = typeof o === "string" ? o : o.value;
          const label = typeof o === "string" ? o : o.label;
          return <option key={value} value={value}>{label}</option>;
        })}
      </select>
      <svg
        width="12" height="12" viewBox="0 0 24 24" aria-hidden="true"
        style={{ position: "absolute", right: 10, top: "50%", marginTop: -6, pointerEvents: "none" }}
      >
        <path d="M6 9l6 6 6-6" fill="none" stroke="var(--text-muted)" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </div>
  );
}
