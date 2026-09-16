The portal's only chart shape for parts of a whole. A ring with no middle, because the hole
carries the number people came for — the ring is context, the text is the answer.

```jsx
<DonutChart
  size={200}
  thickness={24}
  centerHint="of 4,182 devices"
  segments={[
    { label: "Compliant", value: 3461, tone: "success" },
    { label: "Non-compliant", value: 512, tone: "danger" },
    { label: "Unknown", value: 209, tone: "unknown" },
  ]}
/>
```

Order segments good → bad → unknown and the centre needs no configuration: it shows the first
segment's share as a whole percent with that segment's label under it. Override with
`centerValue` / `centerLabel` when the headline is something else, and use `centerHint` for the
denominator.

**Sizes.** 180–220px for a headline chart with its legend; 96–120px with `legend={false}` and a
`caption` for a breakout row underneath — region, managing team, site. Below 96px the percent stops
being readable, so break the data out differently instead.

**Tones, not colours.** `tone: "success" | "danger" | "unknown"` is the compliance triple; `warning`
and `info` exist for other series. Pass `color` with a token only when a series needs a colour the
tones do not cover — never a literal hex.

A faint track ring sits under the segments so a chart at 99% still reads as a ring rather than a
closed circle, and segments are separated by a 1.5-unit gap. Set `gap={0}` for a continuous ring.

The SVG carries a `role="img"` label listing every segment and its count, so the chart is not
silent to a screen reader — but put the same numbers in a table on the page as well. A chart is
not an export.
