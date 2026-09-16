import * as React from "react";

export interface FileUploadProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Accepted-format line shown under the prompt. */
  accept?: string;
  /** Attached files listed under the drop zone. */
  files?: Array<{ name: string; size: string }>;
  onRemove?: (file: { name: string; size: string }) => void;
  disabled?: boolean;
}

export declare function FileUpload(props: FileUploadProps): JSX.Element;
