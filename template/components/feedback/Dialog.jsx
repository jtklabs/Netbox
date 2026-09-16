import React from "react";
import { IconButton } from "../core/IconButton.jsx";

/* Modal for confirmations and short forms. Scrim + centered panel.
   Anything longer than about six fields belongs on its own page instead. */
export function Dialog({ open = true, title, description, children, footer, onClose, width = 520, style, ...rest }) {
  React.useEffect(() => {
    if (!open || !onClose) return;
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      style={{
        position: "absolute", inset: 0, zIndex: "var(--z-dialog)",
        display: "flex", alignItems: "flex-start", justifyContent: "center",
        padding: "10vh var(--space-4) var(--space-4)",
        background: "var(--surface-scrim)",
      }}
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === "string" ? title : undefined}
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "100%", maxWidth: width, maxHeight: "80vh", overflow: "auto",
          background: "var(--surface-raised)",
          backdropFilter: "var(--glass-film-strong)",
          WebkitBackdropFilter: "var(--glass-film-strong)",
          border: "var(--border-width) solid var(--glass-edge-strong)",
          borderRadius: "var(--radius-dialog)",
          boxShadow: "var(--shadow-dialog)",
          ...style,
        }}
        {...rest}
      >
        <header
          style={{
            display: "flex", alignItems: "flex-start", gap: "var(--space-3)",
            padding: "var(--space-4) var(--space-4) var(--space-3)",
            borderBottom: "var(--border-width) solid var(--border-subtle)",
          }}
        >
          <div style={{ flex: 1, minWidth: 0 }}>
            <h3 style={{ margin: 0, fontSize: "var(--text-md)", fontWeight: "var(--weight-semibold)", color: "var(--text-heading)" }}>{title}</h3>
            {description ? (
              <p style={{ margin: "4px 0 0", fontSize: "var(--text-sm)", color: "var(--text-muted)", textWrap: "pretty" }}>{description}</p>
            ) : null}
          </div>
          {onClose ? <IconButton icon="x" label="Close" size="sm" onClick={onClose} /> : null}
        </header>
        {children ? <div style={{ padding: "var(--space-4)" }}>{children}</div> : null}
        {footer ? (
          <footer
            style={{
              display: "flex", alignItems: "center", justifyContent: "flex-end", gap: "var(--gap-inline)",
              padding: "var(--space-3) var(--space-4)",
              borderTop: "var(--border-width) solid var(--border-subtle)",
              background: "var(--surface-sunken)",
              borderRadius: "0 0 var(--radius-dialog) var(--radius-dialog)",
            }}
          >
            {footer}
          </footer>
        ) : null}
      </div>
    </div>
  );
}
