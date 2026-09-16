Path back out of a detail record. Sits above the page title, 12px type.

```jsx
<Breadcrumbs items={[{id:"inventory",label:"Inventory"},{id:"circuits",label:"Circuits"},{label:"CKT-40182"}]} onNavigate={go} />
```

Only on detail and nested screens — never on a top-level list. The last crumb is the current record and is not a link. Three or four levels maximum.
