Multi-line text. Default 4 rows, vertical resize only.

```jsx
<FormField label="Remediation notes" hint="Include ticket references.">
  <Textarea rows={5} />
</FormField>
```

Use `mono` when the content is config, CLI output or a log excerpt. Do not let it grow past the 560px field column — long-form text still reads better narrow.
