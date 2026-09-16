import * as React from "react";

export interface EmptyStateProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Lucide icon name. Default "inbox". */
  icon?: string;
  title?: string;
  /** One sentence saying why it is empty and what to do. */
  description?: string;
  /** A single Button. */
  action?: React.ReactNode;
  /** Tighter padding for inside cards and panels. */
  compact?: boolean;
}

export declare function EmptyState(props: EmptyStateProps): JSX.Element;
