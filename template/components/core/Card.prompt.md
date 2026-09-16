The portal's container primitive — every panel on every screen is a Card.

```jsx
<Card title="Circuit inventory" subtitle="1,284 records" actions={<Button size="sm" iconLeft="download">Export</Button>} padding="none">
  <DataTable columns={cols} rows={rows} />
</Card>
```

1px `--border-default`, 6px radius, no shadow — shadows are reserved for things that float (dropdowns, dialogs, toasts). Use `padding="none"` whenever the body is a table so cell padding provides the inset. `footer` renders a right-aligned sunken band; put form Cancel/Save rows there. Do not nest Cards.
