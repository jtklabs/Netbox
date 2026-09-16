import * as React from "react";

export interface IconButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** Lucide icon name. */
  icon: string;
  /** Required — becomes aria-label and the tooltip title. */
  label: string;
  size?: "sm" | "md" | "lg";
  variant?: "ghost" | "secondary" | "primary" | "danger";
  /** Toggle state for view switchers and filter toggles. */
  selected?: boolean;
  disabled?: boolean;
}

export declare function IconButton(props: IconButtonProps): JSX.Element;
