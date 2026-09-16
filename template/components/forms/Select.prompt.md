Native select with portal chrome. Use for 4+ fixed options.

```jsx
<Select placeholder="All regions" options={["Midwest","Northeast","South","West"]} size="sm" />
```

Two or three mutually exclusive options are better as Radios; an on/off is a Checkbox. It stays a native `<select>` so Django form rendering and keyboard behaviour work unchanged.
