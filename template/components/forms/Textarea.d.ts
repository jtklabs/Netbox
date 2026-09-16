import * as React from "react";

export interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  rows?: number;
  invalid?: boolean;
  /** Mono for config snippets and log paste-ins. */
  mono?: boolean;
}

export declare function Textarea(props: TextareaProps): JSX.Element;
