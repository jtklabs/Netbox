import React from "react";
import { Icon } from "../core/Icon.jsx";
import { Checkbox } from "../forms/Checkbox.jsx";
import { Pagination } from "../navigation/Pagination.jsx";

/* The portal's table. Frosted header, 1px row rules, 40px rows, optional selection.

   Sorting, filtering, search and pagination are BUILT IN and work with no wiring — this
   replaces what the old DataTables plugin did.
   `<DataTable columns={...} rows={...} search filterable paginated />` gives you a search box
   across every column, a per-column filter row, click-to-sort headers and a page footer.

   Sort is type-aware: numbers and dates compare as values, everything else compares as a
   locale string with numeric collation, so CKT-9 sorts before CKT-40182.

   Both can still be driven from outside when the server does the work — pass `sortKey` +
   `onSort` to control sorting, or `onFiltersChange` to take over filtering. Set
   `serverSide` when rows arrive already sorted and filtered and the component should only
   render the controls.

   Column: { key, header, width, align, mono, render(row), sortAccessor(row),
             filter: "text" | "select" | false } */
export function DataTable({
  columns = [], rows = [], selectable = false, selected = [], onSelectedChange,
  sortKey, sortDir, onSort, defaultSortKey, defaultSortDir = "asc",
  search = false, searchPlaceholder = "Filter records", filterable = false,
  filters, onFiltersChange, serverSide = false,
  paginated = false, pageSize: pageSizeProp, pageSizeOptions, page: pageProp, onPageChange, total,
  stickyHeader = false,
  onRowClick, compact = false, emptyState, toolbarActions, style, ...rest
}) {
  const sortControlled = sortKey !== undefined;
  const [selfSort, setSelfSort] = React.useState({ key: defaultSortKey, dir: defaultSortDir });
  const activeSortKey = sortControlled ? sortKey : selfSort.key;
  const activeSortDir = (sortControlled ? sortDir : selfSort.dir) || "asc";

  const filtersControlled = filters !== undefined;
  const [selfFilters, setSelfFilters] = React.useState({});
  const activeFilters = filtersControlled ? filters : selfFilters;
  const [query, setQuery] = React.useState("");
  const [filterRowOpen, setFilterRowOpen] = React.useState(filterable === "open");

  const handleSort = (key) => {
    if (onSort) onSort(key);
    if (!sortControlled) {
      setSelfSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));
    }
  };

  const setFilter = (key, value) => {
    const next = { ...activeFilters };
    if (value === "" || value == null) delete next[key]; else next[key] = value;
    if (!filtersControlled) setSelfFilters(next);
    onFiltersChange && onFiltersChange(next);
  };

  const clearAll = () => {
    setQuery("");
    if (!filtersControlled) setSelfFilters({});
    onFiltersChange && onFiltersChange({});
  };

  /* Unique values per select-filter column, taken from the unfiltered rows so the options
     don't disappear as you narrow the set. */
  const selectOptions = React.useMemo(() => {
    const out = {};
    columns.forEach((c) => {
      if (c.filter !== "select") return;
      out[c.key] = Array.from(new Set(rows.map((r) => cellText(r, c)).filter(Boolean))).sort((a, b) =>
        a.localeCompare(b, undefined, { numeric: true })
      );
    });
    return out;
  }, [columns, rows]);

  const view = React.useMemo(() => {
    if (serverSide) return rows;
    let out = rows;
    const q = query.trim().toLowerCase();
    if (q) out = out.filter((r) => columns.some((c) => cellText(r, c).toLowerCase().includes(q)));
    Object.entries(activeFilters).forEach(([key, value]) => {
      const col = columns.find((c) => c.key === key);
      if (!col || !value) return;
      const v = String(value).toLowerCase();
      out = out.filter((r) =>
        col.filter === "select" ? cellText(r, col).toLowerCase() === v : cellText(r, col).toLowerCase().includes(v)
      );
    });
    if (activeSortKey) {
      const col = columns.find((c) => c.key === activeSortKey);
      const dir = activeSortDir === "desc" ? -1 : 1;
      out = [...out].sort((a, b) => dir * compareRows(a, b, col, activeSortKey));
    }
    return out;
  }, [rows, columns, query, activeFilters, activeSortKey, activeSortDir, serverSide]);

  const filterCount = Object.keys(activeFilters).length + (query.trim() ? 1 : 0);

  /* Pagination. Uncontrolled unless `page` is passed; the page resets whenever the filters
     or the sort change, because page 4 of a different result set means nothing. */
  const pageControlled = pageProp !== undefined;
  const [selfPage, setSelfPage] = React.useState(1);
  const [selfPageSize, setSelfPageSize] = React.useState(pageSizeProp || 25);
  const pageSize = selfPageSize;
  const page = pageControlled ? pageProp : selfPage;
  React.useEffect(() => {
    if (!pageControlled) setSelfPage(1);
  }, [query, activeFilters, activeSortKey, activeSortDir, pageSize]);

  const goToPage = (p) => { if (!pageControlled) setSelfPage(p); onPageChange && onPageChange(p); };
  const rowCount = total != null ? total : view.length;
  const paged = React.useMemo(
    () => (paginated && !serverSide ? view.slice((page - 1) * pageSize, page * pageSize) : view),
    [paginated, serverSide, view, page, pageSize]
  );
  const hasFilterRow = filterable && filterRowOpen;
  const allOn = paged.length > 0 && paged.every((r) => selected.includes(r.id));
  const someOn = selected.length > 0 && !allOn;
  const rowH = compact ? "var(--row-height-compact)" : "var(--row-height)";
  const padY = compact ? "5px" : "var(--pad-cell-y)";

  const toggleAll = () =>
    onSelectedChange &&
    onSelectedChange(allOn ? selected.filter((id) => !paged.some((r) => r.id === id)) : Array.from(new Set([...selected, ...paged.map((r) => r.id)])));
  const toggleOne = (id) =>
    onSelectedChange && onSelectedChange(selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id]);

  const showToolbar = search || filterable || toolbarActions || filterCount > 0;

  return (
    <div style={{ width: "100%", minWidth: 0, ...style }} {...rest}>
      {showToolbar ? (
        <div
          style={{
            display: "flex", alignItems: "center", gap: "var(--gap-inline)", flexWrap: "wrap",
            padding: "var(--space-2) var(--pad-cell-x)",
            borderBottom: "var(--border-width) solid var(--border-subtle)",
          }}
        >
          {search ? (
            <label style={{ position: "relative", display: "flex", alignItems: "center", flex: "0 1 280px", minWidth: 180 }}>
              <span style={{ position: "absolute", left: 9, display: "flex", color: "var(--text-subtle)", pointerEvents: "none" }}>
                <Icon name="search" size={14} />
              </span>
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={searchPlaceholder}
                aria-label={searchPlaceholder}
                style={{ ...fieldStyle, height: 30, paddingLeft: 28 }}
              />
            </label>
          ) : null}
          {filterable ? (
            <ToolbarButton
              icon="sliders-horizontal"
              label={filterRowOpen ? "Hide column filters" : "Column filters"}
              active={filterRowOpen}
              onClick={() => setFilterRowOpen((o) => !o)}
            />
          ) : null}
          {filterCount > 0 ? (
            <>
              <span style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)", fontVariantNumeric: "tabular-nums" }}>
                {view.length.toLocaleString()} of {rows.length.toLocaleString()}
              </span>
              <ToolbarButton icon="x" label="Clear filters" onClick={clearAll} />
            </>
          ) : null}
          <div style={{ flex: 1 }} />
          {toolbarActions}
        </div>
      ) : null}

      {!view.length && emptyState ? (
        emptyState
      ) : (
        /* A horizontal scrollport and a sticky header are mutually exclusive: an element with
           overflow-x:auto is a scroll container on BOTH axes, so the header would pin to a box
           that never scrolls. stickyHeader gives up horizontal scroll to get it. */
        <div style={{ width: "100%", overflowX: stickyHeader ? "visible" : "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "var(--text-base)" }}>
            <thead>
              <tr>
                {selectable ? (
                  <th style={{ ...thStyle, ...(stickyHeader ? stickyTh : staticTh), width: 36, paddingRight: 0 }}>
                    <Checkbox checked={allOn} indeterminate={someOn} onChange={toggleAll} />
                  </th>
                ) : null}
                {columns.map((c) => {
                  const sorted = activeSortKey === c.key;
                  const canSort = c.sortable !== false;
                  return (
                    <SortHeader
                      key={c.key}
                      column={c}
                      sorted={sorted}
                      dir={activeSortDir}
                      canSort={canSort}
                      onSort={() => canSort && handleSort(c.key)}
                      sticky={stickyHeader}
                    />
                  );
                })}
              </tr>
              {hasFilterRow ? (
                <tr>
                  {selectable ? <th style={{ ...filterCellStyle, ...(stickyHeader ? stickyFilterTh : staticTh) }} /> : null}
                  {columns.map((c) => (
                    <th key={c.key} style={{ ...filterCellStyle, ...(stickyHeader ? stickyFilterTh : staticTh) }}>
                      {c.filter === false ? null : c.filter === "select" ? (
                        <select
                          value={activeFilters[c.key] || ""}
                          onChange={(e) => setFilter(c.key, e.target.value)}
                          aria-label={`Filter by ${typeof c.header === "string" ? c.header : c.key}`}
                          style={{ ...fieldStyle, height: 26, paddingLeft: 7, paddingRight: 20 }}
                        >
                          <option value="">All</option>
                          {(selectOptions[c.key] || []).map((o) => (
                            <option key={o} value={o}>{o}</option>
                          ))}
                        </select>
                      ) : (
                        <input
                          value={activeFilters[c.key] || ""}
                          onChange={(e) => setFilter(c.key, e.target.value)}
                          placeholder="—"
                          aria-label={`Filter by ${typeof c.header === "string" ? c.header : c.key}`}
                          style={{ ...fieldStyle, height: 26, textAlign: c.align === "right" ? "right" : "left" }}
                        />
                      )}
                    </th>
                  ))}
                </tr>
              ) : null}
            </thead>
            <tbody>
              {paged.map((row) => (
                <Row
                  key={row.id}
                  row={row}
                  columns={columns}
                  rowH={rowH}
                  padY={padY}
                  selectable={selectable}
                  checked={selected.includes(row.id)}
                  onToggle={toggleOne}
                  onRowClick={onRowClick}
                />
              ))}
              {!view.length ? (
                <tr>
                  <td
                    colSpan={columns.length + (selectable ? 1 : 0)}
                    style={{ padding: "var(--space-8) var(--pad-cell-x)", textAlign: "center", color: "var(--text-muted)", fontSize: "var(--text-sm)" }}
                  >
                    No records match these filters.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      )}
      {paginated && view.length ? (
        <Pagination
          page={page}
          pageSize={pageSize}
          total={rowCount}
          pageSizes={pageSizeOptions || undefined}
          onPageChange={goToPage}
          onPageSizeChange={(n) => { setSelfPageSize(n); goToPage(1); }}
        />
      ) : null}
    </div>
  );
}

/* Plain text for a cell, for searching and sorting. Uses sortAccessor, then the raw field,
   and only falls back to render() output when the value lives entirely in the renderer. */
function cellText(row, col) {
  if (col.sortAccessor) return String(col.sortAccessor(row) ?? "");
  const raw = row[col.key];
  if (raw != null && typeof raw !== "object") return String(raw);
  if (col.render) {
    const node = col.render(row);
    return typeof node === "string" || typeof node === "number" ? String(node) : flattenNode(node);
  }
  return "";
}

function flattenNode(node) {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(flattenNode).join(" ");
  const p = node.props;
  if (!p) return "";
  return flattenNode(p.children) || String(p.status ?? p.label ?? p.value ?? "");
}

function compareRows(a, b, col, key) {
  const av = col && col.sortAccessor ? col.sortAccessor(a) : a[key];
  const bv = col && col.sortAccessor ? col.sortAccessor(b) : b[key];
  if (av == null && bv == null) return 0;
  if (av == null) return 1;
  if (bv == null) return -1;
  if (typeof av === "number" && typeof bv === "number") return av - bv;
  if (av instanceof Date && bv instanceof Date) return av - bv;
  const as = cellText(a, col || { key });
  const bs = cellText(b, col || { key });
  const an = asNumber(as);
  const bn = asNumber(bs);
  if (an != null && bn != null) return an - bn;
  return as.localeCompare(bs, undefined, { numeric: true, sensitivity: "base" });
}

/* A formatted number, or null. Accepts an optional leading currency symbol and an optional
   trailing unit — "$11,900", "10,000 Mbps", "99.94%", "-0.6 pts" all compare as values.
   Anything else (an ID like CKT-40182, an IP, an ISO date) returns null and falls through to
   the string comparison, which already collates those correctly. */
function asNumber(value) {
  const m = String(value).trim().match(/^[^\w\s-]?\s*(-?[\d,]+(?:\.\d+)?)\s*(%|[a-z/]{1,6})?$/i);
  if (!m) return null;
  const n = parseFloat(m[1].replace(/,/g, ""));
  return Number.isNaN(n) ? null : n;
}

function SortHeader({ column: c, sorted, dir, canSort, onSort, sticky }) {
  const [hover, setHover] = React.useState(false);
  return (
    <th
      style={{
        ...thStyle,
        ...(sticky ? stickyTh : staticTh),
        width: c.width,
        textAlign: c.align || "left",
        cursor: canSort ? "pointer" : "default",
        color: sorted ? "var(--text-heading)" : "var(--text-muted)",
        background: hover && canSort ? "var(--glass-hover)" : "var(--surface-sunken)",
      }}
      onClick={onSort}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      aria-sort={sorted ? (dir === "asc" ? "ascending" : "descending") : canSort ? "none" : undefined}
    >
      <span style={{ display: "inline-flex", alignItems: "center", gap: 4, justifyContent: c.align === "right" ? "flex-end" : "flex-start" }}>
        {c.header}
        {sorted ? (
          <Icon name={dir === "asc" ? "arrow-up" : "arrow-down"} size={11} strokeWidth={2.5} />
        ) : canSort && hover ? (
          <Icon name="chevrons-up-down" size={11} strokeWidth={2} color="var(--text-subtle)" />
        ) : null}
      </span>
    </th>
  );
}

function ToolbarButton({ icon, label, active, onClick }) {
  const [hover, setHover] = React.useState(false);
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      aria-pressed={active}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        width: 30, height: 30, flex: "0 0 auto", cursor: "pointer",
        borderRadius: "var(--radius-control)",
        border: `var(--border-width) solid ${active ? "var(--border-strong)" : "transparent"}`,
        background: active ? "var(--action-secondary-bg-active)" : hover ? "var(--action-ghost-bg-hover)" : "transparent",
        color: active ? "var(--text-heading)" : "var(--text-muted)",
        transition: "var(--transition-control)",
      }}
    >
      <Icon name={icon} size={15} />
    </button>
  );
}

const thStyle = {
  padding: "8px var(--pad-cell-x)",
  background: "var(--surface-sunken)",
  backdropFilter: "var(--glass-film-subtle)",
  WebkitBackdropFilter: "var(--glass-film-subtle)",
  borderBottom: "var(--border-width) solid var(--border-default)",
  fontSize: "var(--type-table-header-size)",
  fontWeight: "var(--type-table-header-weight)",
  letterSpacing: "var(--type-table-header-tracking)",
  textTransform: "uppercase",
  color: "var(--text-muted)",
  whiteSpace: "nowrap",
  userSelect: "none",
};

const filterCellStyle = {
  padding: "5px var(--pad-cell-x)",
  background: "var(--surface-sunken)",
  borderBottom: "var(--border-width) solid var(--border-default)",
};

const staticTh = { position: "static" };
const stickyTh = { position: "sticky", top: 0, zIndex: 2 };
const stickyFilterTh = { position: "sticky", top: "var(--row-height-compact)", zIndex: 2 };

const fieldStyle = {
  width: "100%", minWidth: 0,
  padding: "0 8px",
  background: "var(--surface-input)",
  border: "var(--border-width) solid var(--border-input)",
  borderRadius: "var(--radius-control)",
  color: "var(--text-body)",
  fontSize: "var(--text-xs)",
  fontFamily: "inherit",
  outline: "none",
};

function Row({ row, columns, rowH, padY, selectable, checked, onToggle, onRowClick }) {
  const [hover, setHover] = React.useState(false);
  return (
    <tr
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onClick={onRowClick ? () => onRowClick(row) : undefined}
      style={{
        height: rowH,
        background: checked ? "var(--surface-selected)" : hover ? "var(--surface-hover)" : "transparent",
        cursor: onRowClick ? "pointer" : "default",
        transition: "var(--transition-surface)",
      }}
    >
      {selectable ? (
        <td style={{ ...tdStyle, padding: `${padY} 0 ${padY} var(--pad-cell-x)`, width: 36 }} onClick={(e) => e.stopPropagation()}>
          <Checkbox checked={checked} onChange={() => onToggle(row.id)} />
        </td>
      ) : null}
      {columns.map((c) => (
        <td
          key={c.key}
          style={{
            ...tdStyle,
            padding: `${padY} var(--pad-cell-x)`,
            textAlign: c.align || "left",
            fontFamily: c.mono ? "var(--font-mono)" : "inherit",
            fontSize: c.mono ? "var(--type-data-size)" : "inherit",
            fontVariantNumeric: c.mono || c.align === "right" ? "tabular-nums" : undefined,
            whiteSpace: c.wrap ? "normal" : "nowrap",
            color: c.muted ? "var(--text-muted)" : "var(--text-body)",
          }}
        >
          {c.render ? c.render(row) : row[c.key]}
        </td>
      ))}
    </tr>
  );
}

const tdStyle = {
  padding: "var(--pad-cell-y) var(--pad-cell-x)",
  borderBottom: "var(--border-width) solid var(--border-subtle)",
  verticalAlign: "middle",
};
