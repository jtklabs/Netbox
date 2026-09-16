import * as React from "react";

export interface CardProps extends React.HTMLAttributes<HTMLElement> {
  title?: React.ReactNode;
  subtitle?: React.ReactNode;
  /** Buttons/IconButtons pinned right in the header rule. */
  actions?: React.ReactNode;
  /** Right-aligned footer band on --surface-sunken. Form submit rows live here. */
  footer?: React.ReactNode;
  /** "none" when the body is a DataTable — the table draws its own edges. */
  padding?: "none" | "md" | "lg";
  children?: React.ReactNode;
}

export declare function Card(props: CardProps): JSX.Element;
