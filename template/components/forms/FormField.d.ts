import * as React from "react";

export interface FormFieldProps extends React.HTMLAttributes<HTMLDivElement> {
  label?: React.ReactNode;
  /** Must match the id on the control inside. */
  htmlFor?: string;
  /** Persistent helper text under the field. Format guidance, not instructions. */
  hint?: string;
  /** Replaces the hint when present and gets role="alert". */
  error?: string;
  /** Renders a red asterisk. Mark required fields, not optional ones, when most are required. */
  required?: boolean;
  /** Renders "(optional)" instead — use when most fields in the form are required. */
  optional?: boolean;
  /** Defaults to --field-max (560px). Set a narrower value for short inputs. */
  maxWidth?: string | number;
  children?: React.ReactNode;
}

export declare function FormField(props: FormFieldProps): JSX.Element;
