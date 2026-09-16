import * as React from "react";

export interface DataTableColumn {
  key: string;
  header: React.ReactNode;
  /** CSS width for the column, e.g. 120 or "18%". */
  width?: number | string;
  align?: "left" | "right" | "center";
  /** Mono + tabular figures — IPs, IDs, serials, timestamps. */
  mono?: boolean;
  /** Allow the cell to wrap. Default is nowrap. */
  wrap?: boolean;
  /** Renders in --text-muted. */
  muted?: boolean;
  /** Sortable unless false. */
  sortable?: boolean;
  /**
   * Control of the column's filter input. "text" (default) matches a substring; "select"
   * builds a dropdown from the column's distinct values; false gives no filter.
   */
  filter?: "text" | "select" | false;
  /**
   * Value used for sorting, searching and filtering. Give this whenever the cell is rendered
   * as a component — e.g. sortAccessor: row => row.bandwidthMbps for a formatted bandwidth,
   * or row => new Date(row.audited) for a date.
   */
  sortAccessor?: (row: any) => string | number | Date | null | undefined;
  /** Custom cell renderer, e.g. row => <StatusPill status={row.status} />. */
  render?: (row: any) => React.ReactNode;
}

export interface DataTableProps extends React.HTMLAttributes<HTMLDivElement> {
  columns?: DataTableColumn[];
  /** Each row needs a unique `id`. */
  rows?: any[];
  selectable?: boolean;
  selected?: Array<string | number>;
  /** Select-all applies to the rows currently passing the filters, not the whole set. */
  onSelectedChange?: (ids: Array<string | number>) => void;

  /** Sort runs internally unless you pass sortKey — then it is yours to control. */
  sortKey?: string;
  sortDir?: "asc" | "desc";
  onSort?: (key: string) => void;
  /** Initial sort for the uncontrolled case. */
  defaultSortKey?: string;
  defaultSortDir?: "asc" | "desc";

  /** Search box in the toolbar, matching across every column. */
  search?: boolean;
  searchPlaceholder?: string;
  /** Per-column filter row. Pass "open" to have it showing from the start. */
  filterable?: boolean | "open";
  /** Filter values by column key. Omit to let the table manage them. */
  filters?: Record<string, string>;
  onFiltersChange?: (filters: Record<string, string>) => void;
  /** Rows already arrive sorted, filtered and paged — render the controls but do not apply them. */
  serverSide?: boolean;

  /** Footer with a range readout, rows-per-page and prev/next. Runs internally. */
  paginated?: boolean;
  /** Starting rows per page. Default 25. */
  pageSize?: number;
  /** Choices in the rows-per-page select. Default [25, 50, 100]. */
  pageSizeOptions?: number[];
  /** Current page, 1-based. Omit to let the table manage it — it resets on filter or sort change. */
  page?: number;
  onPageChange?: (page: number) => void;
  /** Total row count for the readout when rows are paged server-side. */
  total?: number;

  onRowClick?: (row: any) => void;
  /** 34px rows instead of 40px. For side panels and nested tables. */
  compact?: boolean;
  /** Rendered instead of the table when rows is empty. Pass an EmptyState. */
  emptyState?: React.ReactNode;
  /**
   * Pin the header row while the page scrolls. Off by default, and it costs horizontal
   * scrolling: an element with overflow-x:auto is a scroll container on both axes, so a
   * sticky header inside one pins to a box that never moves. Turn it on for tables that fit
   * their column widths; leave it off for wide ones. The panel wrapping the table must not
   * be a scrollport either — use `overflow: clip` rather than `hidden` to round its corners.
   */
  stickyHeader?: boolean;
  /** Buttons pinned to the right of the toolbar — Export, Add, bulk actions. */
  toolbarActions?: React.ReactNode;
}

export declare function DataTable(props: DataTableProps): JSX.Element;
