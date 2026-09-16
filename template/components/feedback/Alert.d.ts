import * as React from "react";

export interface AlertProps extends React.HTMLAttributes<HTMLDivElement> {
  tone?: "info" | "success" | "warning" | "danger";
  title?: React.ReactNode;
  children?: React.ReactNode;
  /** A Button, usually ghost or link. */
  action?: React.ReactNode;
  onDismiss?: () => void;
  /** Override the tone's default Lucide icon. */
  icon?: string;
}

export declare function Alert(props: AlertProps): JSX.Element;
