import * as React from "react";

export interface SidebarNavItem {
  id: string;
  label: string;
  /** Lucide icon name. Required in practice on top-level items if the rail can collapse. */
  icon?: string;
  href?: string;
  /**
   * Child items — a third level, e.g. Inventory → Global → Cisco. A group with children is a
   * disclosure row rather than a link, and opens itself when the active item is under it.
   * Nesting is recursive, but three levels is the practical limit before the indent runs out.
   */
  items?: SidebarNavItem[];
  /** Open a group on first render even when nothing under it is active. */
  defaultOpen?: boolean;
  /** Count chip on the right — open tasks, overdue reports. Becomes a corner dot when collapsed. */
  count?: number | string;
  /** "danger" turns the count chip crimson. */
  countTone?: "default" | "danger";
  /** Shows an external-link glyph — used by the tool launcher links. */
  external?: boolean;
}

export interface SidebarNavSection {
  id: string;
  /** Uppercase section heading. Omit for an ungrouped block at the top. */
  label?: string;
  items: SidebarNavItem[];
  /** Sections start expanded unless this is false. */
  defaultOpen?: boolean;
}

export interface SidebarNavProps extends React.HTMLAttributes<HTMLElement> {
  /** Portal name. Used as the wordmark when no logo is given, and as the logo's alt text. */
  brand?: string;
  /**
   * Brand image: a URL, or your own node. Replaces the wordmark in a fixed 28px-tall slot,
   * so swapping type for an image does not shift the rail.
   */
  logo?: string | React.ReactNode;
  /** The mark used at 56px. Falls back to `logo`. */
  logoCollapsed?: string | React.ReactNode;
  sections?: SidebarNavSection[];
  activeId?: string;
  onNavigate?: (id: string) => void;
  /**
   * Rendered in a bordered band at the bottom — user chip, theme toggle. Pass a function to
   * shed parts when the rail is collapsed: `footer={({ collapsed }) => …}`. A fixed node is
   * clipped at 56px, so anything with more than one element wants the function form.
   */
  footer?: React.ReactNode | ((state: { collapsed: boolean }) => React.ReactNode);
  /** Shows the collapse toggle in the brand row. Default true. */
  collapsible?: boolean;
  /**
   * Collapsed state. Omit to let the rail manage its own (the normal case) — pass a value
   * only when something outside the rail also needs to drive it.
   */
  collapsed?: boolean;
  onCollapsedChange?: (collapsed: boolean) => void;
  /** localStorage key for the remembered collapsed state. Pass "" to not persist. */
  storageKey?: string;
}

export declare function SidebarNav(props: SidebarNavProps): JSX.Element;
