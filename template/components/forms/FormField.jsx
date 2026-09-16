import React from "react";

/* Label-above-field wrapper. Owns the label, optional/required marker, hint and error.
   Every input in the portal is wrapped in one of these. */
export function FormField({
  label, htmlFor, hint, error, required = false, optional = false,
  children, maxWidth = "var(--field-max)", style, ...rest
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-15)", maxWidth, minWidth: 0, ...style }} {...rest}>
      {label ? (
        <label
          htmlFor={htmlFor}
          style={{
            display: "flex", alignItems: "baseline", gap: "var(--space-1)",
            fontSize: "var(--type-label-size)", fontWeight: "var(--type-label-weight)",
            color: "var(--text-body)", lineHeight: 1.35,
          }}
        >
          {label}
          {required ? <span aria-hidden="true" style={{ color: "var(--text-danger)" }}>*</span> : null}
          {optional && !required ? (
            <span style={{ fontWeight: "var(--weight-regular)", fontSize: "var(--text-xs)", color: "var(--text-subtle)" }}>(optional)</span>
          ) : null}
        </label>
      ) : null}
      {children}
      {error ? (
        <p role="alert" style={{ margin: 0, fontSize: "var(--type-hint-size)", color: "var(--text-danger)", fontWeight: "var(--weight-medium)" }}>{error}</p>
      ) : hint ? (
        <p style={{ margin: 0, fontSize: "var(--type-hint-size)", color: "var(--text-muted)" }}>{hint}</p>
      ) : null}
    </div>
  );
}
