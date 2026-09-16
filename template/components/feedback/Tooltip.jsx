import React from "react";

/* Hover/focus label for icon-only controls and truncated cell values.
   Dark navy chip, 4px offset, no arrow. Never put interactive content inside. */
export function Tooltip({ label, placement = "top", children, style, ...rest }) {
  const [show, setShow] = React.useState(false);
  const pos =
    placement === "bottom" ? { top: "calc(100% + 4px)", left: "50%", transform: "translateX(-50%)" }
    : placement === "left" ? { right: "calc(100% + 4px)", top: "50%", transform: "translateY(-50%)" }
    : placement === "right" ? { left: "calc(100% + 4px)", top: "50%", transform: "translateY(-50%)" }
    : { bottom: "calc(100% + 4px)", left: "50%", transform: "translateX(-50%)" };
  return (
    <span
      style={{ position: "relative", display: "inline-flex", ...style }}
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
      onFocus={() => setShow(true)}
      onBlur={() => setShow(false)}
      {...rest}
    >
      {children}
      {show ? (
        <span
          role="tooltip"
          style={{
            position: "absolute", ...pos, zIndex: "var(--z-tooltip)",
            padding: "4px 8px", maxWidth: 260, whiteSpace: "nowrap",
            background: "var(--surface-tooltip)", color: "#fff",
            backdropFilter: "var(--glass-film-subtle)", WebkitBackdropFilter: "var(--glass-film-subtle)",
            fontSize: "var(--text-xs)", lineHeight: 1.4,
            borderRadius: "var(--radius-sm)",
            boxShadow: "var(--shadow-md)", pointerEvents: "none",
          }}
        >
          {label}
        </span>
      ) : null}
    </span>
  );
}
