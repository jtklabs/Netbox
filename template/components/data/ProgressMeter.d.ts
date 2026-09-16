import * as React from "react";

export interface ProgressMeterProps extends React.HTMLAttributes<HTMLDivElement> {
  value?: number;
  max?: number;
  label?: React.ReactNode;
  /** Right-aligned readout, e.g. "18 of 24". */
  valueText?: string;
  tone?: "brand" | "success" | "warning" | "danger";
  size?: "sm" | "md" | "lg";
}

export declare function ProgressMeter(props: ProgressMeterProps): JSX.Element;
