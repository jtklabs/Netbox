Sub-views of a single record or screen.

```jsx
<Tabs activeId={tab} onChange={setTab} tabs={[
  {id:"overview",label:"Overview"},
  {id:"config",label:"Configuration"},
  {id:"compliance",label:"Compliance",count:3},
  {id:"history",label:"History"},
]}/>
```

Underline tabs, 2px navy indicator, sitting on the 1px page rule. Use them inside a record, not for primary navigation — that is the sidebar's job. Counts are plain muted numbers, not Badges.
