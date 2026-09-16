import React from "react";

/* Switch = a setting that takes effect immediately (notification toggles, feature flags).
   For form values that get submitted with a POST, use Checkbox. */
export function Switch({ label, description, checked, disabled = false, onChange, id, style, ...rest }) {
  const [focus, setFocus] = React.useState(false);
  return (
    <label
      style={{
        display: "inline-flex", alignItems: "center", gap: "var(--space-3)",
        cursor: disabled ? "not-allowed" : "pointer", minWidth: 0,
        color: disabled ? "var(--text-subtle)" : "var(--text-body)",
        ...style,
      }}
    >
      <span style={{ position: "relative", display: "inline-flex", flex: "0 0 auto" }}>
        <input
          id={id} type="checkbox" role="switch" checked={!!checked} disabled={disabled} onChange={onChange}
          onFocus={() => setFocus(true)} onBlur={() => setFocus(false)}
          style={{ position: "absolute", opacity: 0, width: 34, height: 20, margin: 0, cursor: "inherit" }}
          {...rest}
        />
        <span
          aria-hidden="true"
          style={{
            display: "inline-flex", alignItems: "center",
            width: 34, height: 20, padding: 2,
            background: disabled ? "var(--gray-200)" : checked ? "var(--navy-700)" : "var(--gray-300)",
            borderRadius: "var(--radius-pill)",
            boxShadow: focus ? "var(--focus-ring)" : "none",
            transition: "var(--transition-control)",
          }}
        >
          <span
            style={{
              width: 16, height: 16, borderRadius: "var(--radius-pill)", background: "#fff",
              boxShadow: "var(--shadow-xs)",
              transform: checked ? "translateX(14px)" : "translateX(0)",
              transition: `transform var(--duration-fast) var(--ease-standard)`,
            }}
          />
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
