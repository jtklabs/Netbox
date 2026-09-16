Lifecycle state of a record, in tables and record headers.

```jsx
<StatusPill status="overdue" />
<StatusPill status="in review" />
<StatusPill status="compliant" />
```

Known statuses map to a tone and label automatically, so the same word is always the same colour across inventory, compliance and tasks. Pill shape with a solid dot distinguishes it from Badge (square, counts) and Tag (removable filter). Add a new status to the map in `StatusPill.jsx` rather than passing a one-off `tone`.
