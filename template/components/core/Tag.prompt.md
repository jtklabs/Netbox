Removable metadata chip: applied filters, assigned sites, labels on an asset.

```jsx
<Tag icon="map-pin" onRemove={() => clear("region")}>Region: Midwest</Tag>
<Tag>SNMP v3</Tag>
```

With `onRemove` it renders an x affordance — that is the pattern for the active-filter bar above a DataTable. Without it, a read-only chip. Keep the label under about 28 characters; truncate upstream rather than wrapping.
