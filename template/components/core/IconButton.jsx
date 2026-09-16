import React from "react";
import { Icon } from "./Icon.jsx";

const SIZES = { sm: { box: 28, icon: 14 }, md: { box: 34, icon: 16 }, lg: { box: 40, icon: 18 } };

export function IconButton({
  icon, label, size = "md", variant = "ghost", disabled = false, selected = false, style, ...rest
}) {
  const [hover, setHover] = React.useState(false);
  const [focus, setFocus] = React.useState(false);
  const s = SIZES[size] || SIZES.md;
  const skins = {
    ghost: { bg: selected ? "var(--surface-selected)" : "transparent", bgHover: "var(--action-ghost-bg-hover)", fg: selected ? "var(--navy-600)" : "var(--text-muted)", border: "transparent" },
    secondary: { bg: "var(--action-secondary-bg)", bgHover: "var(--action-secondary-bg-hover)", fg: "var(--action-secondary-fg)", border: "var(--border-default)" },
    primary: { bg: "var(--action-primary-bg)", bgHover: "var(--action-primary-bg-hover)", fg: "var(--action-primary-fg)", border: "transparent" },
    danger: { bg: "transparent", bgHover: "var(--danger-tint)", fg: "var(--danger-ink)", border: "transparent" },
  };
  const k = skins[variant] || skins.ghost;
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={selected || undefined}
      disabled={disabled}
      title={label}
      style={{
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        width: s.box, height: s.box, padding: 0,
        color: disabled ? "var(--action-disabled-fg)" : k.fg,
        background: disabled ? "transparent" : hover ? k.bgHover : k.bg,
        border: `var(--border-width) solid ${k.border}`,
        borderRadius: "var(--radius-control)",
        boxShadow: focus && !disabled ? "var(--focus-ring)" : "none",
        cursor: disabled ? "not-allowed" : "pointer",
        transition: "var(--transition-control)", outline: "none",
        ...style,
      }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onFocus={() => setFocus(true)}
      onBlur={() => setFocus(false)}
      {...rest}
    >
      <Icon name={icon} size={s.icon} />
    </button>
  );
}
