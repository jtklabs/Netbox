Confirms something that already happened. Bottom-right, one at a time.

```jsx
<Toast tone="success" title="Task reassigned" action={<Button variant="link" size="sm">Undo</Button>} onDismiss={hide}>Now assigned to D. Okafor.</Toast>
```

Auto-dismiss after about five seconds; keep an Undo action for anything reversible. Validation errors never go in a Toast — they belong in an Alert on the form where the fields are.
