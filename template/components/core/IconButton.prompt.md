Square icon-only button for dense toolbars, table row actions and nav controls.

```jsx
<IconButton icon="refresh-cw" label="Refresh" />
<IconButton icon="list" label="List view" selected />
<IconButton icon="trash-2" label="Delete" variant="danger" />
```

`label` is required — it is the accessible name and the hover tooltip. Default `ghost` variant keeps toolbars quiet; `secondary` when it needs to read as a control next to inputs. Use `selected` for view toggles. Never use an IconButton for a primary action.
