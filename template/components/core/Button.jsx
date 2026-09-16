import React from "react";
import { Icon } from "./Icon.jsx";

const SIZES = {
  sm: { h: "var(--control-height-sm)", px: "10px", fs: "var(--text-sm)", icon: 14 },
  md: { h: "var(--control-height)", px: "var(--pad-control-x)", fs: "var(--text-base)", icon: 16 },
  lg: { h: "var(--control-height-lg)", px: "16px", fs: "var(--text-md)", icon: 18 },
};

const SKINS = {
  primary: {
    bg: "var(--action-primary-bg)", bgHover: "var(--action-primary-bg-hover)", bgActive: "var(--action-primary-bg-active)",
    fg: "var(--action-primary-fg)", border: "transparent", ring: "var(--focus-ring)",
  },
  secondary: {
    bg: "var(--action-secondary-bg)", bgHover: "var(--action-secondary-bg-hover)", bgActive: "var(--action-secondary-bg-active)",
    fg: "var(--action-secondary-fg)", border: "var(--border-default)", ring: "var(--focus-ring)",
  },
  ghost: {
    bg: "transparent", bgHover: "var(--action-ghost-bg-hover)", bgActive: "var(--gray-100)",
    fg: "var(--action-ghost-fg)", border: "transparent", ring: "var(--focus-ring)",
  },
  danger: {
    bg: "var(--action-danger-bg)", bgHover: "var(--action-danger-bg-hover)", bgActive: "var(--action-danger-bg-active)",
    fg: "var(--action-danger-fg)", border: "transparent", ring: "var(--focus-ring-danger)",
  },
  link: {
    bg: "transparent", bgHover: "transparent", bgActive: "transparent",
    fg: "var(--text-link)", border: "transparent", ring: "var(--focus-ring)",
  },
};

export function Button({
  children, variant = "secondary", size = "md", iconLeft, iconRight, loading = false,
  disabled = false, fullWidth = false, type = "button", as = "button", href, style, ...rest
}) {
  const [hover, setHover] = React.useState(false);
  const [active, setActive] = React.useState(false);
  const [focus, setFocus] = React.useState(false);
  const s = SIZES[size] || SIZES.md;
  const k = SKINS[variant] || SKINS.secondary;
  const off = disabled || loading;
  const Tag = as === "a" ? "a" : "button";

  const base = {
    display: "inline-flex", alignItems: "center", justifyContent: "center", gap: "var(--space-15)",
    height: s.h, padding: `0 ${s.px}`, minWidth: 0, boxSizing: "border-box",
    fontFamily: "var(--font-sans)", fontSize: s.fs, fontWeight: "var(--weight-medium)",
    lineHeight: 1, letterSpacing: 0, textDecoration: variant === "link" ? "underline" : "none",
    textUnderlineOffset: "2px", whiteSpace: "nowrap",
    color: off ? "var(--action-disabled-fg)" : k.fg,
    background: off ? (variant === "ghost" || variant === "link" ? "transparent" : "var(--action-disabled-bg)") : active ? k.bgActive : hover ? k.bgHover : k.bg,
    border: `var(--border-width) solid ${off ? (variant === "secondary" ? "var(--border-default)" : "transparent") : k.border}`,
    borderRadius: variant === "link" ? "var(--radius-sm)" : "var(--radius-control)",
    boxShadow: focus && !off ? k.ring : "none",
    cursor: off ? "not-allowed" : "pointer",
    width: fullWidth ? "100%" : undefined,
    padding: variant === "link" ? 0 : `0 ${s.px}`,
    height: variant === "link" ? "auto" : s.h,
    transition: "var(--transition-control)",
    outline: "none",
    ...style,
  };

  return (
    <Tag
      type={Tag === "button" ? type : undefined}
      href={Tag === "a" ? href : undefined}
      disabled={Tag === "button" ? off : undefined}
      aria-busy={loading || undefined}
      style={base}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => { setHover(false); setActive(false); }}
      onMouseDown={() => setActive(true)}
      onMouseUp={() => setActive(false)}
      onFocus={() => setFocus(true)}
      onBlur={() => setFocus(false)}
      {...rest}
    >
      {loading ? <Spinner size={s.icon} /> : iconLeft ? <Icon name={iconLeft} size={s.icon} /> : null}
      {children}
      {iconRight && !loading ? <Icon name={iconRight} size={s.icon} /> : null}
    </Tag>
  );
}

function Spinner({ size }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" style={{ display: "block", animation: "ds-spin 700ms linear infinite" }}>
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeOpacity=".28" strokeWidth="2.5" />
      <path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  );
}
