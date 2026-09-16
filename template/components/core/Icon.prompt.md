Draws a Lucide glyph as inline SVG; the single icon primitive for the whole portal.

```jsx
<Icon name="file-check" size={16} />
<Icon name="triangle-alert" size={14} color="var(--warning-ink)" />
```

The host page must load Lucide first: `<script src="https://unpkg.com/lucide@0.469.0/dist/umd/lucide.js"></script>`. Names accept kebab-case or PascalCase. Sizes: 14 in table cells and badges, 16 in buttons and nav, 18 in page headers. Stroke weight stays at 1.75 — do not thicken icons to add emphasis.
