import React from "react";
import { Icon } from "../core/Icon.jsx";
import { Select } from "../forms/Select.jsx";

/* Table footer pagination: range readout, page size, prev/next. */
export function Pagination({
  page = 1, pageSize = 25, total = 0, pageSizes = [25, 50, 100],
  onPageChange, onPageSizeChange, style, ...rest
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  return (
    <div
      style={{
        display: "flex", alignItems: "center", gap: "var(--space-4)", flexWrap: "wrap",
        padding: "var(--space-2) var(--pad-cell-x)",
        borderTop: "var(--border-width) solid var(--border-subtle)",
        fontSize: "var(--text-sm)", color: "var(--text-muted)",
        ...style,
      }}
      {...rest}
    >
      <span style={{ fontVariantNumeric: "tabular-nums" }}>
        {from.toLocaleString()}–{to.toLocaleString()} of {total.toLocaleString()}
      </span>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
        <span>Rows</span>
        <Select
          size="sm"
          value={String(pageSize)}
          options={pageSizes.map((n) => String(n))}
          onChange={(e) => onPageSizeChange && onPageSizeChange(Number(e.target.value))}
          style={{ width: 72 }}
        />
      </div>
      <div style={{ flex: 1 }} />
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-1)" }}>
        <PageBtn icon="chevron-left" label="Previous page" disabled={page <= 1} onClick={() => onPageChange && onPageChange(page - 1)} />
        <span style={{ padding: "0 var(--space-2)", fontVariantNumeric: "tabular-nums", color: "var(--text-body)" }}>
          Page {page} of {pages}
        </span>
        <PageBtn icon="chevron-right" label="Next page" disabled={page >= pages} onClick={() => onPageChange && onPageChange(page + 1)} />
      </div>
    </div>
  );
}

function PageBtn({ icon, label, disabled, onClick }) {
  const [hover, setHover] = React.useState(false);
  return (
    <button
      type="button" aria-label={label} disabled={disabled} onClick={onClick}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      style={{
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        width: 28, height: 28, padding: 0,
        color: disabled ? "var(--text-subtle)" : "var(--text-body)",
        background: hover && !disabled ? "var(--action-secondary-bg-hover)" : "var(--action-secondary-bg)",
        border: "var(--border-width) solid var(--border-input)",
        borderRadius: "var(--radius-control)",
        cursor: disabled ? "not-allowed" : "pointer",
        transition: "var(--transition-control)",
      }}
    >
      <Icon name={icon} size={14} />
    </button>
  );
}
