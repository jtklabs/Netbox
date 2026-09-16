import React from "react";

export function Textarea({ rows = 4, invalid = false, disabled = false, mono = false, style, ...rest }) {
  const [focus, setFocus] = React.useState(false);
  const border = invalid ? "var(--border-danger)" : focus ? "var(--border-focus)" : "var(--border-input)";
  return (
    <textarea
      rows={rows}
      disabled={disabled}
      aria-invalid={invalid || undefined}
      onFocus={() => setFocus(true)}
      onBlur={() => setFocus(false)}
      style={{
        width: "100%", minWidth: 0, resize: "vertical",
        padding: "8px var(--pad-control-x)",
        fontFamily: mono ? "var(--font-mono)" : "var(--font-sans)",
        fontSize: "var(--text-base)", lineHeight: "var(--leading-normal)",
        color: disabled ? "var(--text-subtle)" : "var(--text-body)",
        background: disabled ? "var(--surface-disabled)" : "var(--surface-input)",
        backdropFilter: "var(--glass-film-subtle)",
        WebkitBackdropFilter: "var(--glass-film-subtle)",
        border: `var(--border-width) solid ${border}`,
        borderRadius: "var(--radius-control)",
        boxShadow: focus ? (invalid ? "var(--focus-ring-danger)" : "var(--focus-ring)") : "none",
        outline: "none", transition: "var(--transition-control)",
        ...style,
      }}
      {...rest}
    />
  );
}
