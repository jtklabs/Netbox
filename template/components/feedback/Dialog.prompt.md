Modal for confirmations and short forms.

```jsx
<Dialog open={open} onClose={close} title="Reassign 4 tasks" description="Assignees are notified by email."
  footer={<><Button onClick={close}>Cancel</Button><Button variant="primary">Reassign</Button></>}>
  <FormField label="Assign to"><Select options={people}/></FormField>
</Dialog>
```

Closes on Escape and scrim click. Footer buttons are right-aligned with the primary last; a destructive confirm uses `variant="danger"` and still keeps Cancel. Widths: 420 for a confirm, 520 default, 720 for a form. Over about six fields, use a page instead of a dialog.
