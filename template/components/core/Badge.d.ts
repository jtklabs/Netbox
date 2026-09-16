import * as React from "react";

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** Counts and short labels. For lifecycle state use StatusPill. */
  tone?: "neutral" | "info" | "success" | "warning" | "danger" | "brand";
  /** Filled instead of tinted — only for counts that must read at a glance. */
  solid?: boolean;
  children?: React.ReactNode;
}

export declare function Badge(props: BadgeProps): JSX.Element;
