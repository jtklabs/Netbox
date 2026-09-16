import React from "react";
import { Icon } from "../core/Icon.jsx";

export function Input({
  size = "md", invalid = false, disabled = false, iconLeft, suffix,
  prefix, type = "text", mono = false, style, ...rest
}) {
  const [focus, setFocus] = React.useState(false);
  const h = size === "sm" ? "var(--control-height-sm)" : size === "lg" ? "var(--control-height-lg)" : "var(--control-height)";
  const border = invalid ? "var(--border-danger)" : focus ? "var(--border-focus)" : "var(--border-input)";
  return (
    <div
      style={{
        display: "flex", alignItems: "center", gap: "var(--space-2)",
        height: h, padding: "0 var(--pad-control-x)",
        background: disabled ? "var(--surface-disabled)" : "var(--surface-input)",
        backdropFilter: "var(--glass-film-subtle)",
        WebkitBackdropFilter: "var(--glass-film-subtle)",
        border: `var(--border-width) solid ${border}`,
        borderRadius: "var(--radius-control)",
        boxShadow: focus ? (invalid ? "var(--focus-ring-danger)" : "var(--focus-ring)") : "none",
        transition: "var(--transition-control)",
        minWidth: 0, ...style,
      }}
    >
      {iconLeft ? <Icon name={iconLeft} size={14} color="var(--text-subtle)" /> : null}
      {prefix ? <span style={{ fontSize: "var(--text-sm)", color: "var(--text-muted)", whiteSpace: "nowrap" }}>{prefix}</span> : null}
      <input
        type={type}
        disabled={disabled}
        aria-invalid={invalid || undefined}
        onFocus={() => setFocus(true)}
        onBlur={() => setFocus(false)}
        style={{
          flex: 1, minWidth: 0, height: "100%", padding: 0, margin: 0,
          border: 0, outline: "none", background: "transparent",
          fontFamily: mono ? "var(--font-mono)" : "var(--font-sans)",
          fontSize: size === "sm" ? "var(--text-sm)" : "var(--text-base)",
          color: disabled ? "var(--text-subtle)" : "var(--text-body)",
          fontVariantNumeric: mono ? "tabular-nums" : undefined,
        }}
        {...rest}
      />
      {suffix ? <span style={{ fontSize: "var(--text-sm)", color: "var(--text-muted)", whiteSpace: "nowrap" }}>{suffix}</span> : null}
    </div>
  );
}
