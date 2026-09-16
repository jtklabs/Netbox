Single-line text control.

```jsx
<Input placeholder="Search inventory" iconLeft="search" size="sm" />
<Input mono defaultValue="10.42.8.0" suffix="/22" />
```

Set `mono` for anything an engineer would read character by character — IPs, VLAN IDs, serials, circuit IDs — so digits align in tables and diffs. `size="sm"` in toolbars and table filter rows, `md` in forms. Always inside a FormField unless it is a toolbar search box.
