import * as React from "react";

export interface TabsProps extends React.HTMLAttributes<HTMLDivElement> {
  tabs?: Array<{ id: string; label: string; icon?: string; count?: number | string; disabled?: boolean }>;
  activeId?: string;
  onChange?: (id: string) => void;
}

export declare function Tabs(props: TabsProps): JSX.Element;
