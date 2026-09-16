A setting that takes effect immediately.

```jsx
<Switch checked={digest} onChange={...} label="Weekly compliance digest" description="Sent Mondays at 07:00 local." />
```

Switch = instant effect (notification preferences, feature flags on the settings screen). Checkbox = a value submitted with the form. Do not put Switches inside a form that has a Save button.
