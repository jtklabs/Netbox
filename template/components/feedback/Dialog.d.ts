import * as React from "react";

export interface DialogProps extends React.HTMLAttributes<HTMLDivElement> {
  open?: boolean;
  title?: React.ReactNode;
  description?: React.ReactNode;
  children?: React.ReactNode;
  /** Buttons, right-aligned on a sunken band. Primary goes last. */
  footer?: React.ReactNode;
  /** Called on Escape, scrim click and the close button. */
  onClose?: () => void;
  /** Max width in px. 420 confirm / 520 default / 720 form. */
  width?: number;
}

export declare function Dialog(props: DialogProps): JSX.Element;
