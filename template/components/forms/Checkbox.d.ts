import * as React from "react";

export interface CheckboxProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: React.ReactNode;
  /** Second line under the label, for consequence or scope. */
  description?: string;
  checked?: boolean;
  /** Table select-all in a partial state. */
  indeterminate?: boolean;
}

export declare function Checkbox(props: CheckboxProps): JSX.Element;
