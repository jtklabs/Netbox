The portal's table. Every list screen is one of these inside a Card with `padding="none"`.
Sorting, filtering, search and pagination are built in — this is what replaced the DataTables plugin.

```jsx
<DataTable
  search searchPlaceholder="Circuit ID, carrier, site, IP"
  filterable
  paginated pageSize={25}
  defaultSortKey="id"
  selectable selected={sel} onSelectedChange={setSel}
  onRowClick={open}
  columns={[
    {key:"id",header:"Circuit ID",mono:true,width:140},
    {key:"carrier",header:"Carrier",filter:"select"},
    {key:"bandwidth",header:"Bandwidth",align:"right",mono:true,
     sortAccessor:r=>r.mbps, render:r=>r.mbps.toLocaleString()+" Mbps"},
    {key:"status",header:"Compliance",width:150,filter:"select",render:r=><StatusPill status={r.status}/>},
  ]}
  rows={rows}
  emptyState={<EmptyState icon="cable" title="No circuits match these filters" description="Clear a filter or widen the date range." compact/>}
/>
```

`search` puts a box in the toolbar matching across every column. `filterable` adds a per-column filter
row behind a toolbar toggle; `filter:"select"` builds that column's dropdown from its distinct values,
`filter:false` gives it none. Headers sort on click and the comparison is type-aware, so `CKT-9`
sorts before `CKT-40182` and 10,000 Mbps sorts above 500.

**Give any column rendered as a component a `sortAccessor`** — it is the value sorting, searching and
filtering actually see. Without one the table falls back to reading the rendered output, which works
for a StatusPill and not for much else.

`paginated` adds the footer — range readout, rows per page (25/50/100 by default), prev/next. The page
resets whenever the filters or the sort change, because page 4 of a different result set means nothing.

Server-side instead: pass `sortKey` + `onSort` to control sorting, `filters` + `onFiltersChange` to
control filtering, `page` + `onPageChange` + `total` to control paging, or `serverSide` when rows
already arrive sorted, filtered and paged and the table should only render the controls.

Frosted uppercase header on `--surface-sunken`; 1px subtle rules between rows; hover wash on the row;
selected rows turn `--surface-selected`. `stickyHeader` pins the header while the page scrolls, but
it trades away horizontal scrolling — an `overflow-x:auto` box scrolls on both axes, so a sticky
header inside one pins to something that never moves. Use it on tables that fit their columns, and
give the wrapping panel `overflow: clip` rather than `hidden` so the panel is not a scrollport
either. Select-all covers the rows on the current page, not the whole set. Set `mono` on any identifier column so values align vertically.
Right-align numbers. Put status in a `render` with StatusPill rather than raw text. Pass an EmptyState
— a bare empty table reads as a broken page.
