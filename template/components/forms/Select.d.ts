import * as React from "react";

export interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  /** Strings, or {value,label} objects. */
  options?: Array<string | { value: string; label: string }>;
  /** Renders as an empty-value first option. */
  placeholder?: string;
  size?: "sm" | "md" | "lg";
  invalid?: boolean;
}

export declare function Select(props: SelectProps): JSX.Element;
