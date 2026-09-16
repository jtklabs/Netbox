Shown when a list, panel or search has nothing to display.

```jsx
<EmptyState icon="clipboard-check" title="No open tasks" description="Everything assigned to you is closed. New assignments appear here." />
<EmptyState compact icon="search" title="No results for \"lumen\"" action={<Button size="sm">Clear search</Button>} />
```

Say why it is empty and what happens next. Use `compact` inside a Card or table body, full padding on a whole screen. One action at most; never two.
