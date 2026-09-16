import * as React from "react";

export interface ToastProps extends React.HTMLAttributes<HTMLDivElement> {
  tone?: "success" | "info" | "warning" | "danger";
  title?: React.ReactNode;
  children?: React.ReactNode;
  onDismiss?: () => void;
  /** Usually an Undo link Button. */
  action?: React.ReactNode;
}

export declare function Toast(props: ToastProps): JSX.Element;
