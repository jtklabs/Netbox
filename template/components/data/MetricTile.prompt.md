A single number on the dashboard. Four or five across, in a grid.

```jsx
<MetricTile label="Assets under management" value="4,182" delta="+38 this month" deltaTone="up" icon="server" />
<MetricTile label="Overdue reports" value="6" deltaTone="down" delta="+2 vs last week" onClick={goOverdue} />
```

Label is uppercase 11px; the value is 30px semibold with tabular figures. `deltaTone` colours the change — but note that for a metric like overdue reports, "up" is bad, so choose the tone by meaning, not by arithmetic. Make the tile clickable when there is an obvious filtered list behind it.

**Tone and direction are separate.** `deltaTone` is the verdict (`positive` green, `negative` red,
`neutral` grey) and `deltaDirection` is the arrow (`up`, `down`, `none`). Half the numbers in this
portal are better when they fall, so a non-compliant count down 44 is
`deltaTone="positive" deltaDirection="down"` — green text, downward arrow. Passing the older
`deltaTone="up"` still sets both at once; use it only where rising genuinely is the good news.
