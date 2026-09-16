import * as React from "react";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** primary = one per view. danger = destructive only. link = inline in prose/tables. */
  variant?: "primary" | "secondary" | "ghost" | "danger" | "link";
  size?: "sm" | "md" | "lg";
  /** Lucide icon name rendered before the label. */
  iconLeft?: string;
  /** Lucide icon name rendered after the label. Use for menus/external links only. */
  iconRight?: string;
  loading?: boolean;
  disabled?: boolean;
  fullWidth?: boolean;
  /** Render as an anchor (pass href) when the action navigates. */
  as?: "button" | "a";
  href?: string;
  children?: React.ReactNode;
}

export declare function Button(props: ButtonProps): JSX.Element;
