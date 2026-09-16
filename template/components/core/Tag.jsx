import React from "react";
import { Icon } from "./Icon.jsx";

/* Removable metadata chip — active filters, assigned sites, tags on an asset. */
export function Tag({ children, onRemove, icon, style, ...rest }) {
  const [hover, setHover] = React.useState(false);
  return (
    <span
      style={{
        display: "inline-flex", alignItems: "center", gap: "var(--space-1)",
        height: 24, padding: onRemove ? "0 4px 0 8px" : "0 8px",
        fontSize: "var(--text-xs)", fontWeight: "var(--weight-medium)", lineHeight: 1,
        color: "var(--text-body)", background: "var(--gray-50)",
        border: "var(--border-width) solid var(--border-default)",
        borderRadius: "var(--radius-md)", whiteSpace: "nowrap",
        ...style,
      }}
      {...rest}
    >
      {icon ? <Icon name={icon} size={12} color="var(--text-muted)" /> : null}
      {children}
      {onRemove ? (
        <button
          type="button"
          aria-label="Remove"
          onClick={onRemove}
          onMouseEnter={() => setHover(true)}
          onMouseLeave={() => setHover(false)}
          style={{
            display: "inline-flex", alignItems: "center", justifyContent: "center",
            width: 16, height: 16, padding: 0, marginLeft: 2,
            color: hover ? "var(--text-body)" : "var(--text-subtle)",
            background: hover ? "var(--gray-200)" : "transparent",
            border: 0, borderRadius: "var(--radius-sm)", cursor: "pointer",
            transition: "var(--transition-control)",
          }}
        >
          <Icon name="x" size={11} strokeWidth={2.25} />
        </button>
      ) : null}
    </span>
  );
}
