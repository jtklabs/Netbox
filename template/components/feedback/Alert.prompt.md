Page-level message tied to the current screen.

```jsx
<Alert tone="danger" title="3 fields need attention">Correct the highlighted fields and submit again.</Alert>
<Alert tone="warning" title="Q3 access review closes 30 Sep" action={<Button variant="link" size="sm">Open review</Button>} />
```

Top of the content column, above the page title's card, full width of the content area. This is where a Django form's non-field errors and validation summaries go. Tinted background, 1px border, no shadow. Dismissible only for informational notices — never for validation errors.
