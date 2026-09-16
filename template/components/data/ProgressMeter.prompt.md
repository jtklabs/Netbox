Completion of something countable — evidence collected, controls passed.

```jsx
<ProgressMeter label="Evidence collected" value={18} max={24} valueText="18 of 24" />
<ProgressMeter value={62} tone="warning" size="sm" />
```

Always pair with `valueText` when the raw counts matter; a bar alone is not an answer an auditor accepts. Tone by meaning: brand for neutral progress, warning under target, danger for a breach, success when complete.
