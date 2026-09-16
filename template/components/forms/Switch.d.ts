import * as React from "react";

export interface SwitchProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: React.ReactNode;
  description?: string;
  checked?: boolean;
}

export declare function Switch(props: SwitchProps): JSX.Element;
