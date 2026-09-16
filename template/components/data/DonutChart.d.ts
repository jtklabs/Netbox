import * as React from "react";

export interface DonutSegment {
  label: string;
  value: number;
  /** Maps to a semantic ink. Compliance uses success → danger → unknown, in that order. */
  tone?: "success" | "danger" | "warning" | "info" | "unknown" | "neutral";
  /** Overrides `tone` with an explicit colour. Use a token, not a literal hex. */
  color?: string;
}

export interface DonutChartProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Ordered good → bad → unknown. Percentages are computed from the values. */
  segments?: DonutSegment[];
  /** Rendered diameter in px. 180–220 for a headline chart, 96–120 for a breakout. */
  size?: number;
  /** Ring thickness in px. Roughly an eighth of `size` reads best. */
  thickness?: number;
  /** Gap between segments, in SVG units (the box is 100 wide). 0 for a continuous ring. */
  gap?: number;
  /** The number in the hole. Defaults to the first segment's share as a whole percent. */
  centerValue?: React.ReactNode;
  /** The line under it. Defaults to the first segment's label. */
  centerLabel?: React.ReactNode;
  /** A third, smaller line — a count or an "of 4,182". Hidden below 160px. */
  centerHint?: React.ReactNode;
  /** Label + value + percent per segment. Turn off on breakout charts. */
  legend?: boolean;
  /** Title under the ring — the region or team a breakout chart covers. */
  caption?: React.ReactNode;
  /** A muted second line under the caption — the group's denominator, e.g. "1,204 devices".
   *  Use this rather than `centerHint` on charts under 160px, where the hint is hidden. */
  captionHint?: React.ReactNode;
  captionHref?: string;
  onCaptionClick?: () => void;
}

/** Semantic tone → token map, exported so a legend elsewhere can match the ring. */
export declare const DONUT_TONES: Record<string, string>;

export declare function DonutChart(props: DonutChartProps): JSX.Element;
