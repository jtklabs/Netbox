import * as React from "react";

export interface BreadcrumbsProps extends React.HTMLAttributes<HTMLElement> {
  /** Last item renders as plain text with aria-current="page". */
  items?: Array<{ id?: string; label: string; href?: string }>;
  onNavigate?: (id: string) => void;
}

export declare function Breadcrumbs(props: BreadcrumbsProps): JSX.Element;
