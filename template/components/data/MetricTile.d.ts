import * as React from "react";

export interface MetricTileProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Uppercase eyebrow label. */
  label: string;
  value: React.ReactNode;
  /** Small unit after the number, e.g. "devices", "%". */
  unit?: string;
  /** Change readout, e.g. "+12 this week". */
  delta?: string;
  /**
   * Whether the change is good or bad news — colour only. Half the numbers here are better
   * when they fall, so tone and direction are separate: a non-compliant count down 44 is
   * "positive". "up"/"down" are the legacy spelling and also set the arrow.
   */
  deltaTone?: "neutral" | "positive" | "negative" | "up" | "down";
  /** Which way the number moved — the arrow glyph. Defaults to whatever `deltaTone` implies. */
  deltaDirection?: "up" | "down" | "none";
  icon?: string;
  footnote?: string;
  onClick?: () => void;
}

export declare function MetricTile(props: MetricTileProps): JSX.Element;
