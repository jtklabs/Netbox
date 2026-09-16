import * as React from "react";

export interface IconProps extends React.SVGAttributes<SVGSVGElement> {
  /** Lucide icon name, kebab-case or PascalCase ("file-check", "FileCheck"). */
  name: string;
  /** Pixel box. 14 in dense tables, 16 default, 18 in page headers. */
  size?: number;
  /** 1.75 is the house weight. Never go above 2. */
  strokeWidth?: number;
  color?: string;
  /** Set only when the icon is the sole meaning; otherwise it stays aria-hidden. */
  label?: string;
}

export declare function Icon(props: IconProps): JSX.Element;
