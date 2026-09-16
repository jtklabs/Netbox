Evidence attachment zone for compliance submissions.

```jsx
<FormField label="Supporting evidence" hint="Attach the exported access review.">
  <FileUpload files={[{name:"q3-access-review.xlsx",size:"1.2 MB"}]} onRemove={drop} />
</FormField>
```

Dashed border marks it as a drop target — it is the only dashed border in the system. Always state accepted formats and the size cap in `accept`. Attached files list below with a Remove action; do not hide the list behind a count.
