import React from "react";
import { Icon } from "../core/Icon.jsx";
import { Button } from "../core/Button.jsx";

/* Evidence upload for compliance submissions. Dashed drop zone + attached-file list.
   Cosmetic only — wire to the Django form's file field in production. */
export function FileUpload({ accept = "PDF, CSV, XLSX up to 25 MB", files = [], onRemove, disabled = false, style, ...rest }) {
  const [over, setOver] = React.useState(false);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)", minWidth: 0, ...style }} {...rest}>
      <div
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); }}
        style={{
          display: "flex", flexDirection: "column", alignItems: "center", gap: "var(--space-2)",
          padding: "var(--space-6) var(--space-4)", textAlign: "center",
          background: over ? "var(--surface-selected)" : "var(--surface-sunken)",
          border: `1px dashed ${over ? "var(--border-focus)" : "var(--border-strong)"}`,
          borderRadius: "var(--radius-card)",
          transition: "var(--transition-control)",
          opacity: disabled ? 0.6 : 1,
        }}
      >
        <Icon name="upload-cloud" size={22} color="var(--text-subtle)" />
        <div style={{ fontSize: "var(--text-sm)", color: "var(--text-body)" }}>
          Drag files here or <span style={{ color: "var(--text-link)", fontWeight: "var(--weight-medium)" }}>browse</span>
        </div>
        <div style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>{accept}</div>
      </div>
      {files.length ? (
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
          {files.map((f) => (
            <li
              key={f.name}
              style={{
                display: "flex", alignItems: "center", gap: "var(--space-2)",
                padding: "var(--space-15) var(--space-2)",
                background: "var(--surface-input)",
                border: "var(--border-width) solid var(--border-subtle)",
                borderRadius: "var(--radius-md)",
                fontSize: "var(--text-sm)",
              }}
            >
              <Icon name="paperclip" size={14} color="var(--text-subtle)" />
              <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.name}</span>
              <span style={{ color: "var(--text-muted)", fontSize: "var(--text-xs)", fontVariantNumeric: "tabular-nums" }}>{f.size}</span>
              {onRemove ? <Button variant="ghost" size="sm" onClick={() => onRemove(f)}>Remove</Button> : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
