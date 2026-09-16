import * as React from "react";

export interface PaginationProps extends React.HTMLAttributes<HTMLDivElement> {
  page?: number;
  pageSize?: number;
  total?: number;
  pageSizes?: number[];
  onPageChange?: (page: number) => void;
  onPageSizeChange?: (size: number) => void;
}

export declare function Pagination(props: PaginationProps): JSX.Element;
