The portal's action control — use for anything that submits, navigates or opens a dialog.

```jsx
<Button variant="primary" iconLeft="plus">Add asset</Button>
<Button>Cancel</Button>
<Button variant="ghost" size="sm" iconLeft="download">Export CSV</Button>
<Button variant="danger" iconLeft="trash-2">Delete record</Button>
```

One `primary` per view, always the rightmost button in a footer row. `secondary` is the default for everything else. `ghost` for toolbar and table-row actions. `danger` only for irreversible operations, and never as the only button in a dialog. `link` for inline actions inside sentences and table cells. Sizes: `sm` in toolbars and table rows, `md` everywhere else, `lg` only on login and single-action pages. Pass `loading` while a Django form POSTs — it keeps the width and swaps the icon for a spinner.
