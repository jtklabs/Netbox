Table footer: range readout, rows-per-page, prev/next.

```jsx
<Pagination page={page} pageSize={size} total={1284} onPageChange={setPage} onPageSizeChange={setSize} />
```

Render it inside the Card that holds the table, below the table, with `padding="none"` on the Card. Always show the absolute range and total — engineers use it to confirm a filter did what they expected. Numbered page buttons are deliberately absent; page counts here run to the hundreds.
