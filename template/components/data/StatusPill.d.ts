import * as React from "react";

export interface StatusPillProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** Known keys map to a tone and label automatically: active, compliant, passed,
   *  approved, submitted, in review, draft, planned, decommissioned, due soon,
   *  pending, exception, overdue, failed, non-compliant. */
  status?: string;
  /** Override the mapped tone. */
  tone?: "neutral" | "info" | "success" | "warning" | "danger";
  /** Override the mapped label. */
  children?: React.ReactNode;
}

export declare function StatusPill(props: StatusPillProps): JSX.Element;
