import * as React from "react";

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  size?: "sm" | "md" | "lg";
  invalid?: boolean;
  /** Leading Lucide icon — search fields and lookups only. */
  iconLeft?: string;
  /** Static text before the value, e.g. "AS". */
  prefix?: string;
  /** Static text after the value, e.g. "Mbps", "/32". */
  suffix?: string;
  /** Tabular mono for IPs, ASNs, serials, circuit IDs. */
  mono?: boolean;
}

export declare function Input(props: InputProps): JSX.Element;
