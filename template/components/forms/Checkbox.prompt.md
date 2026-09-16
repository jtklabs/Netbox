Boolean form value, and the table row-selection control.

```jsx
<Checkbox checked={all} indeterminate={some} onChange={toggleAll} />
<Checkbox label="Exclude decommissioned assets" description="Hides 142 records from this report." />
```

`indeterminate` is for a select-all header when only some rows are checked. Use `description` to state the consequence rather than lengthening the label. For settings that apply instantly, use Switch.
