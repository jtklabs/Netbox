Wraps every control in the portal: label above, hint or error below.

```jsx
<FormField label="Circuit ID" htmlFor="ckt" required hint="Carrier-assigned identifier, e.g. CKT-40182">
  <Input id="ckt" mono placeholder="CKT-00000" />
</FormField>
```

Labels sit above fields — that is the portal convention, no exceptions. `error` replaces `hint` and announces via role="alert"; pair it with `invalid` on the control so the border turns red too. Mark `required` when required fields are the minority; mark `optional` when they are the majority. Stack fields with `--gap-field` (16px).
