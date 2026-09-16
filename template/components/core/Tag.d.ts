import * as React from "react";

export interface TagProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** Omit to render a static chip; provide it to show the remove affordance. */
  onRemove?: () => void;
  /** Optional leading Lucide icon. */
  icon?: string;
  children?: React.ReactNode;
}

export declare function Tag(props: TagProps): JSX.Element;
