import * as React from "react";

export interface TooltipProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** Short text — a few words. Not a place for instructions. */
  label: React.ReactNode;
  placement?: "top" | "bottom" | "left" | "right";
  children?: React.ReactNode;
}

export declare function Tooltip(props: TooltipProps): JSX.Element;
