import React from "react";

/* Donut chart — a ring with the headline number in the hole. The portal's only chart shape
   for parts of a whole: compliance posture, coverage, task states.

   No middle, by design: the hole carries the number people came for, so the ring is the
   context and the text is the answer. Segments are ordered good → bad → unknown and the
   centre defaults to the first segment's share.

   Sizes: 180–220px for the headline chart, 96–120px for a breakout row underneath. Below
   96px drop the legend and let the caption carry the labels. */
export const DONUT_TONES = {
  success: "var(--success-ink)",
  danger: "var(--danger-ink)",
  warning: "var(--warning-ink)",
  info: "var(--info-ink)",
  unknown: "var(--gray-400)",
  neutral: "var(--gray-400)",
};

export function DonutChart({
  segments = [], size = 200, thickness = 24, gap = 1.5,
  centerValue, centerLabel, centerHint,
  legend = true, caption, captionHint, captionHref, onCaptionClick, style, ...rest
}) {
  const total = segments.reduce((sum, s) => sum + (Number(s.value) || 0), 0);
  const first = segments[0];
  const share = total && first ? (Number(first.value) || 0) / total : 0;

  /* The SVG works in a 100-unit box, so a px thickness has to be scaled into it. */
  const t = (thickness / size) * 100;
  const r = 50 - t / 2;
  const C = 2 * Math.PI * r;
  const visible = segments.filter((s) => (Number(s.value) || 0) > 0);

  let acc = 0;
  const arcs = segments.map((s, i) => {
    const value = Number(s.value) || 0;
    const frac = total ? value / total : 0;
    const len = Math.max(0, frac * C - (visible.length > 1 ? gap : 0));
    const arc = (
      <circle
        key={s.label || i}
        cx="50" cy="50" r={r} fill="none"
        stroke={s.color || DONUT_TONES[s.tone] || DONUT_TONES.neutral}
        strokeWidth={t}
        strokeDasharray={`${len} ${Math.max(0, C - len)}`}
        strokeDashoffset={-acc * C}
      />
    );
    acc += frac;
    return value > 0 ? arc : null;
  });

  const centreNumber = centerValue != null ? centerValue : Math.round(share * 100) + "%";
  const centreCaption = centerLabel != null ? centerLabel : first ? first.label : null;
  const numberSize = Math.max(18, Math.round(size * 0.185));

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "var(--space-3)", minWidth: 0, ...style }} {...rest}>
      <div style={{ position: "relative", width: size, height: size, flex: "0 0 auto" }}>
        <svg
          viewBox="0 0 100 100" width={size} height={size}
          role="img"
          aria-label={
            segments.map((s) => `${s.label}: ${(Number(s.value) || 0).toLocaleString()}`).join(", ") ||
            "No data"
          }
          style={{ display: "block", transform: "rotate(-90deg)" }}
        >
          <circle cx="50" cy="50" r={r} fill="none" stroke="var(--border-subtle)" strokeWidth={t} />
          {arcs}
        </svg>
        <div
          style={{
            position: "absolute", inset: 0, display: "flex", flexDirection: "column",
            alignItems: "center", justifyContent: "center", gap: 2, textAlign: "center",
            padding: thickness + 6, pointerEvents: "none",
          }}
        >
          <span style={{ fontSize: numberSize, fontWeight: "var(--weight-semibold)", letterSpacing: "-0.02em", color: "var(--text-heading)", fontVariantNumeric: "tabular-nums", lineHeight: 1 }}>
            {centreNumber}
          </span>
          {centreCaption ? (
            <span style={{ fontSize: size < 130 ? "var(--text-2xs)" : "var(--text-xs)", color: "var(--text-muted)", lineHeight: 1.3 }}>{centreCaption}</span>
          ) : null}
          {centerHint && size >= 160 ? (
            <span style={{ fontSize: "var(--text-2xs)", color: "var(--text-subtle)", fontVariantNumeric: "tabular-nums", lineHeight: 1.3 }}>{centerHint}</span>
          ) : null}
        </div>
      </div>

      {caption ? (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 1, minWidth: 0 }}>
          {captionHref || onCaptionClick ? (
            <a
              href={captionHref || "#"}
              onClick={onCaptionClick ? (e) => { e.preventDefault(); onCaptionClick(); } : undefined}
              style={{ fontSize: "var(--text-sm)", fontWeight: "var(--weight-medium)", textAlign: "center" }}
            >
              {caption}
            </a>
          ) : (
            <span style={{ fontSize: "var(--text-sm)", fontWeight: "var(--weight-medium)", color: "var(--text-heading)", textAlign: "center" }}>{caption}</span>
          )}
          {/* The denominator a small ring needs to mean anything. centerHint is hidden below
              160px, so on a breakout chart the count belongs here instead. */}
          {captionHint ? (
            <span style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)", fontVariantNumeric: "tabular-nums", textAlign: "center" }}>{captionHint}</span>
          ) : null}
        </div>
      ) : null}

      {legend ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, width: "100%", minWidth: 0 }}>
          {segments.map((s, i) => {
            const value = Number(s.value) || 0;
            return (
              <div key={s.label || i} style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", fontSize: "var(--text-sm)", minWidth: 0 }}>
                <span
                  style={{
                    width: 8, height: 8, flex: "0 0 auto", borderRadius: 2,
                    background: s.color || DONUT_TONES[s.tone] || DONUT_TONES.neutral,
                  }}
                />
                <span style={{ flex: 1, minWidth: 0, color: "var(--text-body)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.label}</span>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--type-data-size)", fontVariantNumeric: "tabular-nums", color: "var(--text-body)" }}>{value.toLocaleString()}</span>
                <span style={{ width: 46, textAlign: "right", fontVariantNumeric: "tabular-nums", color: "var(--text-muted)" }}>
                  {total ? Math.round((value / total) * 100) : 0}%
                </span>
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
