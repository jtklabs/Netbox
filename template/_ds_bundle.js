/* @ds-bundle: {"format":4,"namespace":"PortalDesignSystem_986bf0","components":[{"name":"Badge","sourcePath":"components/core/Badge.jsx"},{"name":"Button","sourcePath":"components/core/Button.jsx"},{"name":"Card","sourcePath":"components/core/Card.jsx"},{"name":"Icon","sourcePath":"components/core/Icon.jsx"},{"name":"IconButton","sourcePath":"components/core/IconButton.jsx"},{"name":"Tag","sourcePath":"components/core/Tag.jsx"},{"name":"DataTable","sourcePath":"components/data/DataTable.jsx"},{"name":"DONUT_TONES","sourcePath":"components/data/DonutChart.jsx"},{"name":"DonutChart","sourcePath":"components/data/DonutChart.jsx"},{"name":"EmptyState","sourcePath":"components/data/EmptyState.jsx"},{"name":"MetricTile","sourcePath":"components/data/MetricTile.jsx"},{"name":"ProgressMeter","sourcePath":"components/data/ProgressMeter.jsx"},{"name":"StatusPill","sourcePath":"components/data/StatusPill.jsx"},{"name":"Alert","sourcePath":"components/feedback/Alert.jsx"},{"name":"Dialog","sourcePath":"components/feedback/Dialog.jsx"},{"name":"Toast","sourcePath":"components/feedback/Toast.jsx"},{"name":"Tooltip","sourcePath":"components/feedback/Tooltip.jsx"},{"name":"Checkbox","sourcePath":"components/forms/Checkbox.jsx"},{"name":"FileUpload","sourcePath":"components/forms/FileUpload.jsx"},{"name":"FormField","sourcePath":"components/forms/FormField.jsx"},{"name":"Input","sourcePath":"components/forms/Input.jsx"},{"name":"Radio","sourcePath":"components/forms/Radio.jsx"},{"name":"Select","sourcePath":"components/forms/Select.jsx"},{"name":"Switch","sourcePath":"components/forms/Switch.jsx"},{"name":"Textarea","sourcePath":"components/forms/Textarea.jsx"},{"name":"Breadcrumbs","sourcePath":"components/navigation/Breadcrumbs.jsx"},{"name":"Pagination","sourcePath":"components/navigation/Pagination.jsx"},{"name":"SidebarNav","sourcePath":"components/navigation/SidebarNav.jsx"},{"name":"Tabs","sourcePath":"components/navigation/Tabs.jsx"}],"sourceHashes":{"components/core/Badge.jsx":"85c6e1a041ab","components/core/Button.jsx":"0b26ea781c3b","components/core/Card.jsx":"c88efd7cc143","components/core/Icon.jsx":"461e46c6345c","components/core/IconButton.jsx":"931e3d1f3917","components/core/Tag.jsx":"fc41fe2e034e","components/data/DataTable.jsx":"debdac13899d","components/data/DonutChart.jsx":"556328a7609a","components/data/EmptyState.jsx":"0f4234b416c1","components/data/MetricTile.jsx":"3b4411853feb","components/data/ProgressMeter.jsx":"3bef332af37a","components/data/StatusPill.jsx":"e7cd3b1a89df","components/feedback/Alert.jsx":"3e12b7675b9a","components/feedback/Dialog.jsx":"5127c4d8a9ac","components/feedback/Toast.jsx":"7da3c4592c8d","components/feedback/Tooltip.jsx":"bd3e749a5bce","components/forms/Checkbox.jsx":"e2410e95f0d8","components/forms/FileUpload.jsx":"3160d9b3e13b","components/forms/FormField.jsx":"8bd16ee4210a","components/forms/Input.jsx":"5609fd853eb1","components/forms/Radio.jsx":"652fa340e101","components/forms/Select.jsx":"b1200cd770fa","components/forms/Switch.jsx":"62b4ce728aa4","components/forms/Textarea.jsx":"bb0e51de752c","components/navigation/Breadcrumbs.jsx":"94c6386f7ae4","components/navigation/Pagination.jsx":"1d930009b5e4","components/navigation/SidebarNav.jsx":"7a7fcc328e87","components/navigation/Tabs.jsx":"d7418561fb3d","ui_kits/nova-portal/AppShell.jsx":"229fbfa9a75b","ui_kits/nova-portal/AuthAndSettingsScreens.jsx":"9148ec5a8813","ui_kits/nova-portal/ComplianceFormScreen.jsx":"fe5a76ac9971","ui_kits/nova-portal/ComplianceReportScreen.jsx":"fa5ef4dc713e","ui_kits/nova-portal/DashboardScreen.jsx":"e8efa4e9134e","ui_kits/nova-portal/InventoryDetailScreen.jsx":"31401d26734f","ui_kits/nova-portal/InventoryListScreen.jsx":"d85587775692","ui_kits/nova-portal/MultiStepFormScreen.jsx":"50d7cd0d8fcc","ui_kits/nova-portal/TaskScreens.jsx":"17adc9a1e41f","ui_kits/nova-portal/ToolLauncherScreen.jsx":"ffdd5b4d2ffb","ui_kits/nova-portal/data.js":"68521b2c7676"},"inlinedExternals":[],"unexposedExports":[]} */

(() => {

const __ds_ns = (window.PortalDesignSystem_986bf0 = window.PortalDesignSystem_986bf0 || {});

const __ds_scope = {};

(__ds_ns.__errors = __ds_ns.__errors || []);

// components/core/Badge.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const TONES = {
  neutral: ["var(--neutral-tint)", "var(--neutral-ink)", "var(--neutral-edge)"],
  info: ["var(--info-tint)", "var(--info-ink)", "var(--info-edge)"],
  success: ["var(--success-tint)", "var(--success-ink)", "var(--success-edge)"],
  warning: ["var(--warning-tint)", "var(--warning-ink)", "var(--warning-edge)"],
  danger: ["var(--danger-tint)", "var(--danger-ink)", "var(--danger-edge)"],
  brand: ["var(--navy-50)", "var(--navy-600)", "var(--navy-100)"]
};

/* Square-cornered count/label chip. For lifecycle state use StatusPill instead. */
function Badge({
  children,
  tone = "neutral",
  solid = false,
  style,
  ...rest
}) {
  const [tint, ink, edge] = TONES[tone] || TONES.neutral;
  return /*#__PURE__*/React.createElement("span", _extends({
    style: {
      display: "inline-flex",
      alignItems: "center",
      gap: "var(--space-1)",
      height: 20,
      padding: "0 6px",
      fontSize: "var(--text-2xs)",
      fontWeight: "var(--weight-semibold)",
      letterSpacing: "var(--tracking-wide)",
      lineHeight: 1,
      whiteSpace: "nowrap",
      color: solid ? "var(--text-inverse)" : ink,
      background: solid ? ink : tint,
      border: `var(--border-width) solid ${solid ? "transparent" : edge}`,
      borderRadius: "var(--radius-sm)",
      fontVariantNumeric: "tabular-nums",
      ...style
    }
  }, rest), children);
}
Object.assign(__ds_scope, { Badge });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Badge.jsx", error: String((e && e.message) || e) }); }

// components/core/Card.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/* The portal's only container. A frosted panel: 8px radius, light rim, soft spread.
   Use `title` + `actions` for the standard header rule; `padding="none"` for tables. */
function Card({
  title,
  subtitle,
  actions,
  footer,
  padding = "md",
  children,
  style,
  ...rest
}) {
  const pad = padding === "none" ? 0 : padding === "lg" ? "var(--pad-card-lg)" : "var(--pad-card)";
  return /*#__PURE__*/React.createElement("section", _extends({
    style: {
      display: "flex",
      flexDirection: "column",
      minWidth: 0,
      background: "var(--surface-card)",
      backdropFilter: "var(--glass-film)",
      WebkitBackdropFilter: "var(--glass-film)",
      border: "var(--border-width) solid var(--glass-edge)",
      borderRadius: "var(--radius-card)",
      boxShadow: "var(--shadow-card)",
      ...style
    }
  }, rest), title || actions ? /*#__PURE__*/React.createElement("header", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--space-3)",
      padding: "var(--space-3) var(--pad-card)",
      borderBottom: "var(--border-width) solid var(--border-subtle)",
      minHeight: 44
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      minWidth: 0,
      flex: 1
    }
  }, title ? /*#__PURE__*/React.createElement("h3", {
    style: {
      margin: 0,
      fontSize: "var(--type-card-title-size)",
      fontWeight: "var(--type-card-title-weight)",
      color: "var(--text-heading)",
      letterSpacing: 0
    }
  }, title) : null, subtitle ? /*#__PURE__*/React.createElement("p", {
    style: {
      margin: "2px 0 0",
      fontSize: "var(--text-xs)",
      color: "var(--text-muted)"
    }
  }, subtitle) : null), actions ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--gap-inline)"
    }
  }, actions) : null) : null, /*#__PURE__*/React.createElement("div", {
    style: {
      padding: pad,
      minWidth: 0,
      flex: 1
    }
  }, children), footer ? /*#__PURE__*/React.createElement("footer", {
    style: {
      display: "flex",
      alignItems: "center",
      justifyContent: "flex-end",
      gap: "var(--gap-inline)",
      padding: "var(--space-3) var(--pad-card)",
      borderTop: "var(--border-width) solid var(--border-subtle)",
      background: "var(--surface-sunken)",
      borderRadius: "0 0 var(--radius-card) var(--radius-card)"
    }
  }, footer) : null);
}
Object.assign(__ds_scope, { Card });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Card.jsx", error: String((e && e.message) || e) }); }

// components/core/Icon.jsx
try { (() => {
const pascal = s => String(s || "").replace(/(^|[-_ ])(\w)/g, (_, __, c) => c.toUpperCase());

/* Renders a Lucide glyph. Lucide is loaded from CDN by the host page
   (<script src="https://unpkg.com/lucide@0.469.0/dist/umd/lucide.js">);
   this component reads the icon data off window.lucide and draws it as React. */
function Icon({
  name,
  size = 16,
  strokeWidth = 1.75,
  color = "currentColor",
  label,
  style,
  ...rest
}) {
  const lib = typeof window !== "undefined" && window.lucide && window.lucide.icons || null;
  const node = lib ? lib[pascal(name)] || lib[name] : null;
  let children = [];
  if (Array.isArray(node)) children = node[0] === "svg" ? node[2] || [] : node;
  return React.createElement("svg", {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: color,
    strokeWidth: strokeWidth,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    role: label ? "img" : undefined,
    "aria-label": label || undefined,
    "aria-hidden": label ? undefined : "true",
    style: {
      display: "block",
      flex: "0 0 auto",
      ...style
    },
    ...rest
  }, children.map((child, i) => {
    const tag = Array.isArray(child) ? child[0] : null;
    const attrs = Array.isArray(child) ? child[1] || {} : {};
    return tag ? React.createElement(tag, {
      key: i,
      ...attrs
    }) : null;
  }));
}
Object.assign(__ds_scope, { Icon });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Icon.jsx", error: String((e && e.message) || e) }); }

// components/core/Button.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const SIZES = {
  sm: {
    h: "var(--control-height-sm)",
    px: "10px",
    fs: "var(--text-sm)",
    icon: 14
  },
  md: {
    h: "var(--control-height)",
    px: "var(--pad-control-x)",
    fs: "var(--text-base)",
    icon: 16
  },
  lg: {
    h: "var(--control-height-lg)",
    px: "16px",
    fs: "var(--text-md)",
    icon: 18
  }
};
const SKINS = {
  primary: {
    bg: "var(--action-primary-bg)",
    bgHover: "var(--action-primary-bg-hover)",
    bgActive: "var(--action-primary-bg-active)",
    fg: "var(--action-primary-fg)",
    border: "transparent",
    ring: "var(--focus-ring)"
  },
  secondary: {
    bg: "var(--action-secondary-bg)",
    bgHover: "var(--action-secondary-bg-hover)",
    bgActive: "var(--action-secondary-bg-active)",
    fg: "var(--action-secondary-fg)",
    border: "var(--border-default)",
    ring: "var(--focus-ring)"
  },
  ghost: {
    bg: "transparent",
    bgHover: "var(--action-ghost-bg-hover)",
    bgActive: "var(--gray-100)",
    fg: "var(--action-ghost-fg)",
    border: "transparent",
    ring: "var(--focus-ring)"
  },
  danger: {
    bg: "var(--action-danger-bg)",
    bgHover: "var(--action-danger-bg-hover)",
    bgActive: "var(--action-danger-bg-active)",
    fg: "var(--action-danger-fg)",
    border: "transparent",
    ring: "var(--focus-ring-danger)"
  },
  link: {
    bg: "transparent",
    bgHover: "transparent",
    bgActive: "transparent",
    fg: "var(--text-link)",
    border: "transparent",
    ring: "var(--focus-ring)"
  }
};
function Button({
  children,
  variant = "secondary",
  size = "md",
  iconLeft,
  iconRight,
  loading = false,
  disabled = false,
  fullWidth = false,
  type = "button",
  as = "button",
  href,
  style,
  ...rest
}) {
  const [hover, setHover] = React.useState(false);
  const [active, setActive] = React.useState(false);
  const [focus, setFocus] = React.useState(false);
  const s = SIZES[size] || SIZES.md;
  const k = SKINS[variant] || SKINS.secondary;
  const off = disabled || loading;
  const Tag = as === "a" ? "a" : "button";
  const base = {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: "var(--space-15)",
    height: s.h,
    padding: `0 ${s.px}`,
    minWidth: 0,
    boxSizing: "border-box",
    fontFamily: "var(--font-sans)",
    fontSize: s.fs,
    fontWeight: "var(--weight-medium)",
    lineHeight: 1,
    letterSpacing: 0,
    textDecoration: variant === "link" ? "underline" : "none",
    textUnderlineOffset: "2px",
    whiteSpace: "nowrap",
    color: off ? "var(--action-disabled-fg)" : k.fg,
    background: off ? variant === "ghost" || variant === "link" ? "transparent" : "var(--action-disabled-bg)" : active ? k.bgActive : hover ? k.bgHover : k.bg,
    border: `var(--border-width) solid ${off ? variant === "secondary" ? "var(--border-default)" : "transparent" : k.border}`,
    borderRadius: variant === "link" ? "var(--radius-sm)" : "var(--radius-control)",
    boxShadow: focus && !off ? k.ring : "none",
    cursor: off ? "not-allowed" : "pointer",
    width: fullWidth ? "100%" : undefined,
    padding: variant === "link" ? 0 : `0 ${s.px}`,
    height: variant === "link" ? "auto" : s.h,
    transition: "var(--transition-control)",
    outline: "none",
    ...style
  };
  return /*#__PURE__*/React.createElement(Tag, _extends({
    type: Tag === "button" ? type : undefined,
    href: Tag === "a" ? href : undefined,
    disabled: Tag === "button" ? off : undefined,
    "aria-busy": loading || undefined,
    style: base,
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => {
      setHover(false);
      setActive(false);
    },
    onMouseDown: () => setActive(true),
    onMouseUp: () => setActive(false),
    onFocus: () => setFocus(true),
    onBlur: () => setFocus(false)
  }, rest), loading ? /*#__PURE__*/React.createElement(Spinner, {
    size: s.icon
  }) : iconLeft ? /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: iconLeft,
    size: s.icon
  }) : null, children, iconRight && !loading ? /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: iconRight,
    size: s.icon
  }) : null);
}
function Spinner({
  size
}) {
  return /*#__PURE__*/React.createElement("svg", {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    "aria-hidden": "true",
    style: {
      display: "block",
      animation: "ds-spin 700ms linear infinite"
    }
  }, /*#__PURE__*/React.createElement("circle", {
    cx: "12",
    cy: "12",
    r: "9",
    fill: "none",
    stroke: "currentColor",
    strokeOpacity: ".28",
    strokeWidth: "2.5"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M21 12a9 9 0 0 0-9-9",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2.5",
    strokeLinecap: "round"
  }));
}
Object.assign(__ds_scope, { Button });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Button.jsx", error: String((e && e.message) || e) }); }

// components/core/IconButton.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const SIZES = {
  sm: {
    box: 28,
    icon: 14
  },
  md: {
    box: 34,
    icon: 16
  },
  lg: {
    box: 40,
    icon: 18
  }
};
function IconButton({
  icon,
  label,
  size = "md",
  variant = "ghost",
  disabled = false,
  selected = false,
  style,
  ...rest
}) {
  const [hover, setHover] = React.useState(false);
  const [focus, setFocus] = React.useState(false);
  const s = SIZES[size] || SIZES.md;
  const skins = {
    ghost: {
      bg: selected ? "var(--surface-selected)" : "transparent",
      bgHover: "var(--action-ghost-bg-hover)",
      fg: selected ? "var(--navy-600)" : "var(--text-muted)",
      border: "transparent"
    },
    secondary: {
      bg: "var(--action-secondary-bg)",
      bgHover: "var(--action-secondary-bg-hover)",
      fg: "var(--action-secondary-fg)",
      border: "var(--border-default)"
    },
    primary: {
      bg: "var(--action-primary-bg)",
      bgHover: "var(--action-primary-bg-hover)",
      fg: "var(--action-primary-fg)",
      border: "transparent"
    },
    danger: {
      bg: "transparent",
      bgHover: "var(--danger-tint)",
      fg: "var(--danger-ink)",
      border: "transparent"
    }
  };
  const k = skins[variant] || skins.ghost;
  return /*#__PURE__*/React.createElement("button", _extends({
    type: "button",
    "aria-label": label,
    "aria-pressed": selected || undefined,
    disabled: disabled,
    title: label,
    style: {
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      width: s.box,
      height: s.box,
      padding: 0,
      color: disabled ? "var(--action-disabled-fg)" : k.fg,
      background: disabled ? "transparent" : hover ? k.bgHover : k.bg,
      border: `var(--border-width) solid ${k.border}`,
      borderRadius: "var(--radius-control)",
      boxShadow: focus && !disabled ? "var(--focus-ring)" : "none",
      cursor: disabled ? "not-allowed" : "pointer",
      transition: "var(--transition-control)",
      outline: "none",
      ...style
    },
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    onFocus: () => setFocus(true),
    onBlur: () => setFocus(false)
  }, rest), /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: icon,
    size: s.icon
  }));
}
Object.assign(__ds_scope, { IconButton });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/IconButton.jsx", error: String((e && e.message) || e) }); }

// components/core/Tag.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/* Removable metadata chip — active filters, assigned sites, tags on an asset. */
function Tag({
  children,
  onRemove,
  icon,
  style,
  ...rest
}) {
  const [hover, setHover] = React.useState(false);
  return /*#__PURE__*/React.createElement("span", _extends({
    style: {
      display: "inline-flex",
      alignItems: "center",
      gap: "var(--space-1)",
      height: 24,
      padding: onRemove ? "0 4px 0 8px" : "0 8px",
      fontSize: "var(--text-xs)",
      fontWeight: "var(--weight-medium)",
      lineHeight: 1,
      color: "var(--text-body)",
      background: "var(--gray-50)",
      border: "var(--border-width) solid var(--border-default)",
      borderRadius: "var(--radius-md)",
      whiteSpace: "nowrap",
      ...style
    }
  }, rest), icon ? /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: icon,
    size: 12,
    color: "var(--text-muted)"
  }) : null, children, onRemove ? /*#__PURE__*/React.createElement("button", {
    type: "button",
    "aria-label": "Remove",
    onClick: onRemove,
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      width: 16,
      height: 16,
      padding: 0,
      marginLeft: 2,
      color: hover ? "var(--text-body)" : "var(--text-subtle)",
      background: hover ? "var(--gray-200)" : "transparent",
      border: 0,
      borderRadius: "var(--radius-sm)",
      cursor: "pointer",
      transition: "var(--transition-control)"
    }
  }, /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: "x",
    size: 11,
    strokeWidth: 2.25
  })) : null);
}
Object.assign(__ds_scope, { Tag });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Tag.jsx", error: String((e && e.message) || e) }); }

// components/data/DonutChart.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/* Donut chart — a ring with the headline number in the hole. The portal's only chart shape
   for parts of a whole: compliance posture, coverage, task states.

   No middle, by design: the hole carries the number people came for, so the ring is the
   context and the text is the answer. Segments are ordered good → bad → unknown and the
   centre defaults to the first segment's share.

   Sizes: 180–220px for the headline chart, 96–120px for a breakout row underneath. Below
   96px drop the legend and let the caption carry the labels. */
const DONUT_TONES = {
  success: "var(--success-ink)",
  danger: "var(--danger-ink)",
  warning: "var(--warning-ink)",
  info: "var(--info-ink)",
  unknown: "var(--gray-400)",
  neutral: "var(--gray-400)"
};
function DonutChart({
  segments = [],
  size = 200,
  thickness = 24,
  gap = 1.5,
  centerValue,
  centerLabel,
  centerHint,
  legend = true,
  caption,
  captionHint,
  captionHref,
  onCaptionClick,
  style,
  ...rest
}) {
  const total = segments.reduce((sum, s) => sum + (Number(s.value) || 0), 0);
  const first = segments[0];
  const share = total && first ? (Number(first.value) || 0) / total : 0;

  /* The SVG works in a 100-unit box, so a px thickness has to be scaled into it. */
  const t = thickness / size * 100;
  const r = 50 - t / 2;
  const C = 2 * Math.PI * r;
  const visible = segments.filter(s => (Number(s.value) || 0) > 0);
  let acc = 0;
  const arcs = segments.map((s, i) => {
    const value = Number(s.value) || 0;
    const frac = total ? value / total : 0;
    const len = Math.max(0, frac * C - (visible.length > 1 ? gap : 0));
    const arc = /*#__PURE__*/React.createElement("circle", {
      key: s.label || i,
      cx: "50",
      cy: "50",
      r: r,
      fill: "none",
      stroke: s.color || DONUT_TONES[s.tone] || DONUT_TONES.neutral,
      strokeWidth: t,
      strokeDasharray: `${len} ${Math.max(0, C - len)}`,
      strokeDashoffset: -acc * C
    });
    acc += frac;
    return value > 0 ? arc : null;
  });
  const centreNumber = centerValue != null ? centerValue : Math.round(share * 100) + "%";
  const centreCaption = centerLabel != null ? centerLabel : first ? first.label : null;
  const numberSize = Math.max(18, Math.round(size * 0.185));
  return /*#__PURE__*/React.createElement("div", _extends({
    style: {
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      gap: "var(--space-3)",
      minWidth: 0,
      ...style
    }
  }, rest), /*#__PURE__*/React.createElement("div", {
    style: {
      position: "relative",
      width: size,
      height: size,
      flex: "0 0 auto"
    }
  }, /*#__PURE__*/React.createElement("svg", {
    viewBox: "0 0 100 100",
    width: size,
    height: size,
    role: "img",
    "aria-label": segments.map(s => `${s.label}: ${(Number(s.value) || 0).toLocaleString()}`).join(", ") || "No data",
    style: {
      display: "block",
      transform: "rotate(-90deg)"
    }
  }, /*#__PURE__*/React.createElement("circle", {
    cx: "50",
    cy: "50",
    r: r,
    fill: "none",
    stroke: "var(--border-subtle)",
    strokeWidth: t
  }), arcs), /*#__PURE__*/React.createElement("div", {
    style: {
      position: "absolute",
      inset: 0,
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      gap: 2,
      textAlign: "center",
      padding: thickness + 6,
      pointerEvents: "none"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: numberSize,
      fontWeight: "var(--weight-semibold)",
      letterSpacing: "-0.02em",
      color: "var(--text-heading)",
      fontVariantNumeric: "tabular-nums",
      lineHeight: 1
    }
  }, centreNumber), centreCaption ? /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: size < 130 ? "var(--text-2xs)" : "var(--text-xs)",
      color: "var(--text-muted)",
      lineHeight: 1.3
    }
  }, centreCaption) : null, centerHint && size >= 160 ? /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: "var(--text-2xs)",
      color: "var(--text-subtle)",
      fontVariantNumeric: "tabular-nums",
      lineHeight: 1.3
    }
  }, centerHint) : null)), caption ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      gap: 1,
      minWidth: 0
    }
  }, captionHref || onCaptionClick ? /*#__PURE__*/React.createElement("a", {
    href: captionHref || "#",
    onClick: onCaptionClick ? e => {
      e.preventDefault();
      onCaptionClick();
    } : undefined,
    style: {
      fontSize: "var(--text-sm)",
      fontWeight: "var(--weight-medium)",
      textAlign: "center"
    }
  }, caption) : /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: "var(--text-sm)",
      fontWeight: "var(--weight-medium)",
      color: "var(--text-heading)",
      textAlign: "center"
    }
  }, caption), captionHint ? /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: "var(--text-xs)",
      color: "var(--text-muted)",
      fontVariantNumeric: "tabular-nums",
      textAlign: "center"
    }
  }, captionHint) : null) : null, legend ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 6,
      width: "100%",
      minWidth: 0
    }
  }, segments.map((s, i) => {
    const value = Number(s.value) || 0;
    return /*#__PURE__*/React.createElement("div", {
      key: s.label || i,
      style: {
        display: "flex",
        alignItems: "center",
        gap: "var(--space-2)",
        fontSize: "var(--text-sm)",
        minWidth: 0
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        width: 8,
        height: 8,
        flex: "0 0 auto",
        borderRadius: 2,
        background: s.color || DONUT_TONES[s.tone] || DONUT_TONES.neutral
      }
    }), /*#__PURE__*/React.createElement("span", {
      style: {
        flex: 1,
        minWidth: 0,
        color: "var(--text-body)",
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap"
      }
    }, s.label), /*#__PURE__*/React.createElement("span", {
      style: {
        fontFamily: "var(--font-mono)",
        fontSize: "var(--type-data-size)",
        fontVariantNumeric: "tabular-nums",
        color: "var(--text-body)"
      }
    }, value.toLocaleString()), /*#__PURE__*/React.createElement("span", {
      style: {
        width: 46,
        textAlign: "right",
        fontVariantNumeric: "tabular-nums",
        color: "var(--text-muted)"
      }
    }, total ? Math.round(value / total * 100) : 0, "%"));
  })) : null);
}
Object.assign(__ds_scope, { DONUT_TONES, DonutChart });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/DonutChart.jsx", error: String((e && e.message) || e) }); }

// components/data/EmptyState.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
function EmptyState({
  icon = "inbox",
  title,
  description,
  action,
  compact = false,
  style,
  ...rest
}) {
  return /*#__PURE__*/React.createElement("div", _extends({
    style: {
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      gap: "var(--space-2)",
      textAlign: "center",
      padding: compact ? "var(--space-6) var(--space-4)" : "var(--space-12) var(--space-6)",
      ...style
    }
  }, rest), /*#__PURE__*/React.createElement("span", {
    style: {
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      width: 40,
      height: 40,
      marginBottom: "var(--space-1)",
      background: "var(--surface-sunken)",
      border: "var(--border-width) solid var(--border-subtle)",
      borderRadius: "var(--radius-lg)",
      color: "var(--text-subtle)"
    }
  }, /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: icon,
    size: 18
  })), title ? /*#__PURE__*/React.createElement("h4", {
    style: {
      margin: 0,
      fontSize: "var(--text-base)",
      fontWeight: "var(--weight-semibold)",
      color: "var(--text-heading)"
    }
  }, title) : null, description ? /*#__PURE__*/React.createElement("p", {
    style: {
      margin: 0,
      maxWidth: 380,
      fontSize: "var(--text-sm)",
      color: "var(--text-muted)",
      textWrap: "pretty"
    }
  }, description) : null, action ? /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: "var(--space-2)"
    }
  }, action) : null);
}
Object.assign(__ds_scope, { EmptyState });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/EmptyState.jsx", error: String((e && e.message) || e) }); }

// components/data/MetricTile.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/* Single number on the dashboard. Bordered like a Card but its own component
   because the metric type role and delta line are fixed.

   The delta has TWO independent properties, because half the numbers in this portal are
   better when they fall: `deltaTone` sets the colour (is this good or bad news) and
   `deltaDirection` sets the arrow (which way did it move). A non-compliant count dropping by
   44 is deltaTone="positive" deltaDirection="down" — green text, downward arrow. The legacy
   "up"/"down" tone values still work and drive both, so existing tiles are unaffected. */
function MetricTile({
  label,
  value,
  unit,
  delta,
  deltaTone = "neutral",
  deltaDirection,
  icon,
  footnote,
  onClick,
  style,
  ...rest
}) {
  const [hover, setHover] = React.useState(false);
  const positive = deltaTone === "positive" || deltaTone === "up";
  const negative = deltaTone === "negative" || deltaTone === "down";
  const deltaColor = positive ? "var(--success-ink)" : negative ? "var(--danger-ink)" : "var(--text-muted)";
  /* No explicit direction: fall back to the legacy reading where the tone was the arrow. */
  const direction = deltaDirection || (deltaTone === "up" ? "up" : deltaTone === "down" ? "down" : "none");
  return /*#__PURE__*/React.createElement("div", _extends({
    onClick: onClick,
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-1)",
      minWidth: 0,
      padding: "var(--pad-card)",
      background: "var(--surface-card)",
      backdropFilter: "var(--glass-film)",
      WebkitBackdropFilter: "var(--glass-film)",
      border: "var(--border-width) solid " + (hover && onClick ? "var(--border-strong)" : "var(--glass-edge)"),
      borderRadius: "var(--radius-card)",
      cursor: onClick ? "pointer" : "default",
      transition: "var(--transition-control)",
      ...style
    }
  }, rest), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--space-2)"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      flex: 1,
      minWidth: 0,
      fontSize: "var(--type-eyebrow-size)",
      letterSpacing: "var(--type-eyebrow-tracking)",
      textTransform: "uppercase",
      fontWeight: "var(--weight-semibold)",
      color: "var(--text-muted)"
    }
  }, label), icon ? /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: icon,
    size: 15,
    color: "var(--text-subtle)"
  }) : null), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "baseline",
      gap: "var(--space-15)"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: "var(--type-metric-size)",
      fontWeight: "var(--type-metric-weight)",
      letterSpacing: "var(--tracking-tight)",
      color: "var(--text-heading)",
      fontVariantNumeric: "tabular-nums",
      lineHeight: 1.1
    }
  }, value), unit ? /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: "var(--text-sm)",
      color: "var(--text-muted)"
    }
  }, unit) : null), delta || footnote ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--space-2)",
      fontSize: "var(--text-xs)"
    }
  }, delta ? /*#__PURE__*/React.createElement("span", {
    style: {
      display: "inline-flex",
      alignItems: "center",
      gap: 3,
      color: deltaColor,
      fontWeight: "var(--weight-medium)"
    }
  }, direction === "up" ? /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: "trending-up",
    size: 12
  }) : direction === "down" ? /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: "trending-down",
    size: 12
  }) : null, delta) : null, footnote ? /*#__PURE__*/React.createElement("span", {
    style: {
      color: "var(--text-muted)"
    }
  }, footnote) : null) : null);
}
Object.assign(__ds_scope, { MetricTile });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/MetricTile.jsx", error: String((e && e.message) || e) }); }

// components/data/ProgressMeter.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const TONES = {
  brand: "var(--navy-500)",
  success: "var(--success-ink)",
  warning: "var(--warning-ink)",
  danger: "var(--danger-ink)"
};

/* Horizontal completion bar — compliance coverage, evidence collected, upload progress.
   4px track, square ends, no animation on load. */
function ProgressMeter({
  value = 0,
  max = 100,
  label,
  valueText,
  tone = "brand",
  size = "md",
  style,
  ...rest
}) {
  const pct = Math.max(0, Math.min(100, value / (max || 1) * 100));
  const h = size === "sm" ? 4 : size === "lg" ? 8 : 6;
  return /*#__PURE__*/React.createElement("div", _extends({
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-1)",
      minWidth: 0,
      ...style
    }
  }, rest), label || valueText ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "baseline",
      gap: "var(--space-2)",
      fontSize: "var(--text-xs)"
    }
  }, label ? /*#__PURE__*/React.createElement("span", {
    style: {
      flex: 1,
      minWidth: 0,
      color: "var(--text-body)"
    }
  }, label) : null, valueText ? /*#__PURE__*/React.createElement("span", {
    style: {
      color: "var(--text-muted)",
      fontVariantNumeric: "tabular-nums",
      fontWeight: "var(--weight-medium)"
    }
  }, valueText) : null) : null, /*#__PURE__*/React.createElement("div", {
    role: "progressbar",
    "aria-valuenow": value,
    "aria-valuemin": 0,
    "aria-valuemax": max,
    style: {
      height: h,
      width: "100%",
      background: "var(--gray-100)",
      borderRadius: "var(--radius-sm)",
      overflow: "hidden"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      height: "100%",
      width: pct + "%",
      background: TONES[tone] || TONES.brand,
      borderRadius: "var(--radius-sm)"
    }
  })));
}
Object.assign(__ds_scope, { ProgressMeter });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/ProgressMeter.jsx", error: String((e && e.message) || e) }); }

// components/data/StatusPill.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const STATES = {
  active: ["success", "Active"],
  compliant: ["success", "Compliant"],
  passed: ["success", "Passed"],
  approved: ["success", "Approved"],
  submitted: ["info", "Submitted"],
  inreview: ["info", "In review"],
  draft: ["neutral", "Draft"],
  planned: ["neutral", "Planned"],
  decommissioned: ["neutral", "Decommissioned"],
  duesoon: ["warning", "Due soon"],
  pending: ["warning", "Pending"],
  exception: ["warning", "Exception"],
  overdue: ["danger", "Overdue"],
  failed: ["danger", "Failed"],
  noncompliant: ["danger", "Non-compliant"]
};
const TONES = {
  neutral: ["var(--neutral-tint)", "var(--neutral-ink)", "var(--neutral-edge)"],
  info: ["var(--info-tint)", "var(--info-ink)", "var(--info-edge)"],
  success: ["var(--success-tint)", "var(--success-ink)", "var(--success-edge)"],
  warning: ["var(--warning-tint)", "var(--warning-ink)", "var(--warning-edge)"],
  danger: ["var(--danger-tint)", "var(--danger-ink)", "var(--danger-edge)"]
};

/* Lifecycle state in tables and record headers. Pill-shaped with a solid dot,
   so it never reads as a Badge (square) or a Tag (removable). */
function StatusPill({
  status,
  tone,
  children,
  style,
  ...rest
}) {
  const key = String(status || "").toLowerCase().replace(/[\s-_]/g, "");
  const [mappedTone, mappedLabel] = STATES[key] || ["neutral", status];
  const t = tone || mappedTone;
  const [tint, ink, edge] = TONES[t] || TONES.neutral;
  return /*#__PURE__*/React.createElement("span", _extends({
    style: {
      display: "inline-flex",
      alignItems: "center",
      gap: 5,
      height: 22,
      padding: "0 9px 0 7px",
      fontSize: "var(--text-xs)",
      fontWeight: "var(--weight-medium)",
      lineHeight: 1,
      color: ink,
      background: tint,
      border: `var(--border-width) solid ${edge}`,
      borderRadius: "var(--radius-status)",
      whiteSpace: "nowrap",
      ...style
    }
  }, rest), /*#__PURE__*/React.createElement("span", {
    "aria-hidden": "true",
    style: {
      width: 6,
      height: 6,
      borderRadius: "var(--radius-pill)",
      background: ink,
      flex: "0 0 auto"
    }
  }), children || mappedLabel);
}
Object.assign(__ds_scope, { StatusPill });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/StatusPill.jsx", error: String((e && e.message) || e) }); }

// components/feedback/Alert.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const TONES = {
  info: ["var(--info-tint)", "var(--info-ink)", "var(--info-edge)", "info"],
  success: ["var(--success-tint)", "var(--success-ink)", "var(--success-edge)", "circle-check"],
  warning: ["var(--warning-tint)", "var(--warning-ink)", "var(--warning-edge)", "triangle-alert"],
  danger: ["var(--danger-tint)", "var(--danger-ink)", "var(--danger-edge)", "octagon-alert"]
};

/* Page-level message: validation summary after a failed POST, maintenance notice,
   "this record is locked pending review". Tinted, 1px border, no shadow. */
function Alert({
  tone = "info",
  title,
  children,
  action,
  onDismiss,
  icon,
  style,
  ...rest
}) {
  const [tint, ink, edge, defaultIcon] = TONES[tone] || TONES.info;
  return /*#__PURE__*/React.createElement("div", _extends({
    role: tone === "danger" ? "alert" : "status",
    style: {
      display: "flex",
      alignItems: "flex-start",
      gap: "var(--space-3)",
      padding: "var(--space-3) var(--space-4)",
      background: tint,
      border: `var(--border-width) solid ${edge}`,
      borderRadius: "var(--radius-md)",
      minWidth: 0,
      ...style
    }
  }, rest), /*#__PURE__*/React.createElement("span", {
    style: {
      color: ink,
      marginTop: 1,
      flex: "0 0 auto"
    }
  }, /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: icon || defaultIcon,
    size: 16
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minWidth: 0
    }
  }, title ? /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: "var(--text-base)",
      fontWeight: "var(--weight-semibold)",
      color: ink,
      marginBottom: children ? 2 : 0
    }
  }, title) : null, children ? /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: "var(--text-sm)",
      color: "var(--text-body)",
      textWrap: "pretty"
    }
  }, children) : null, action ? /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: "var(--space-2)"
    }
  }, action) : null), onDismiss ? /*#__PURE__*/React.createElement(__ds_scope.IconButton, {
    icon: "x",
    label: "Dismiss",
    size: "sm",
    onClick: onDismiss
  }) : null);
}
Object.assign(__ds_scope, { Alert });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/feedback/Alert.jsx", error: String((e && e.message) || e) }); }

// components/feedback/Dialog.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/* Modal for confirmations and short forms. Scrim + centered panel.
   Anything longer than about six fields belongs on its own page instead. */
function Dialog({
  open = true,
  title,
  description,
  children,
  footer,
  onClose,
  width = 520,
  style,
  ...rest
}) {
  React.useEffect(() => {
    if (!open || !onClose) return;
    const onKey = e => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return /*#__PURE__*/React.createElement("div", {
    style: {
      position: "absolute",
      inset: 0,
      zIndex: "var(--z-dialog)",
      display: "flex",
      alignItems: "flex-start",
      justifyContent: "center",
      padding: "10vh var(--space-4) var(--space-4)",
      background: "var(--surface-scrim)"
    },
    onClick: onClose
  }, /*#__PURE__*/React.createElement("div", _extends({
    role: "dialog",
    "aria-modal": "true",
    "aria-label": typeof title === "string" ? title : undefined,
    onClick: e => e.stopPropagation(),
    style: {
      width: "100%",
      maxWidth: width,
      maxHeight: "80vh",
      overflow: "auto",
      background: "var(--surface-raised)",
      backdropFilter: "var(--glass-film-strong)",
      WebkitBackdropFilter: "var(--glass-film-strong)",
      border: "var(--border-width) solid var(--glass-edge-strong)",
      borderRadius: "var(--radius-dialog)",
      boxShadow: "var(--shadow-dialog)",
      ...style
    }
  }, rest), /*#__PURE__*/React.createElement("header", {
    style: {
      display: "flex",
      alignItems: "flex-start",
      gap: "var(--space-3)",
      padding: "var(--space-4) var(--space-4) var(--space-3)",
      borderBottom: "var(--border-width) solid var(--border-subtle)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("h3", {
    style: {
      margin: 0,
      fontSize: "var(--text-md)",
      fontWeight: "var(--weight-semibold)",
      color: "var(--text-heading)"
    }
  }, title), description ? /*#__PURE__*/React.createElement("p", {
    style: {
      margin: "4px 0 0",
      fontSize: "var(--text-sm)",
      color: "var(--text-muted)",
      textWrap: "pretty"
    }
  }, description) : null), onClose ? /*#__PURE__*/React.createElement(__ds_scope.IconButton, {
    icon: "x",
    label: "Close",
    size: "sm",
    onClick: onClose
  }) : null), children ? /*#__PURE__*/React.createElement("div", {
    style: {
      padding: "var(--space-4)"
    }
  }, children) : null, footer ? /*#__PURE__*/React.createElement("footer", {
    style: {
      display: "flex",
      alignItems: "center",
      justifyContent: "flex-end",
      gap: "var(--gap-inline)",
      padding: "var(--space-3) var(--space-4)",
      borderTop: "var(--border-width) solid var(--border-subtle)",
      background: "var(--surface-sunken)",
      borderRadius: "0 0 var(--radius-dialog) var(--radius-dialog)"
    }
  }, footer) : null));
}
Object.assign(__ds_scope, { Dialog });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/feedback/Dialog.jsx", error: String((e && e.message) || e) }); }

// components/feedback/Toast.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const TONES = {
  success: ["var(--success-ink)", "circle-check"],
  info: ["var(--info-ink)", "info"],
  warning: ["var(--warning-ink)", "triangle-alert"],
  danger: ["var(--danger-ink)", "octagon-alert"]
};

/* Confirmation of something that already happened ("Task reassigned", "Export queued").
   Bottom-right, one at a time, auto-dismiss. Never use one to report a validation error —
   that belongs in an Alert at the top of the form. */
function Toast({
  tone = "success",
  title,
  children,
  onDismiss,
  action,
  style,
  ...rest
}) {
  const [ink, icon] = TONES[tone] || TONES.success;
  return /*#__PURE__*/React.createElement("div", _extends({
    role: "status",
    style: {
      display: "flex",
      alignItems: "flex-start",
      gap: "var(--space-3)",
      width: 340,
      padding: "var(--space-3)",
      background: "var(--surface-raised)",
      backdropFilter: "var(--glass-film-strong)",
      WebkitBackdropFilter: "var(--glass-film-strong)",
      border: "var(--border-width) solid var(--glass-edge-strong)",
      borderRadius: "var(--radius-md)",
      boxShadow: "var(--shadow-lg)",
      animation: "ds-toast-in var(--duration-base) var(--ease-out)",
      ...style
    }
  }, rest), /*#__PURE__*/React.createElement("span", {
    style: {
      color: ink,
      marginTop: 1,
      flex: "0 0 auto"
    }
  }, /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: icon,
    size: 16
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minWidth: 0
    }
  }, title ? /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: "var(--text-base)",
      fontWeight: "var(--weight-medium)",
      color: "var(--text-strong)"
    }
  }, title) : null, children ? /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 2,
      fontSize: "var(--text-sm)",
      color: "var(--text-muted)"
    }
  }, children) : null, action ? /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: "var(--space-2)"
    }
  }, action) : null), onDismiss ? /*#__PURE__*/React.createElement(__ds_scope.IconButton, {
    icon: "x",
    label: "Dismiss",
    size: "sm",
    onClick: onDismiss
  }) : null);
}
Object.assign(__ds_scope, { Toast });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/feedback/Toast.jsx", error: String((e && e.message) || e) }); }

// components/feedback/Tooltip.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/* Hover/focus label for icon-only controls and truncated cell values.
   Dark navy chip, 4px offset, no arrow. Never put interactive content inside. */
function Tooltip({
  label,
  placement = "top",
  children,
  style,
  ...rest
}) {
  const [show, setShow] = React.useState(false);
  const pos = placement === "bottom" ? {
    top: "calc(100% + 4px)",
    left: "50%",
    transform: "translateX(-50%)"
  } : placement === "left" ? {
    right: "calc(100% + 4px)",
    top: "50%",
    transform: "translateY(-50%)"
  } : placement === "right" ? {
    left: "calc(100% + 4px)",
    top: "50%",
    transform: "translateY(-50%)"
  } : {
    bottom: "calc(100% + 4px)",
    left: "50%",
    transform: "translateX(-50%)"
  };
  return /*#__PURE__*/React.createElement("span", _extends({
    style: {
      position: "relative",
      display: "inline-flex",
      ...style
    },
    onMouseEnter: () => setShow(true),
    onMouseLeave: () => setShow(false),
    onFocus: () => setShow(true),
    onBlur: () => setShow(false)
  }, rest), children, show ? /*#__PURE__*/React.createElement("span", {
    role: "tooltip",
    style: {
      position: "absolute",
      ...pos,
      zIndex: "var(--z-tooltip)",
      padding: "4px 8px",
      maxWidth: 260,
      whiteSpace: "nowrap",
      background: "var(--surface-tooltip)",
      color: "#fff",
      backdropFilter: "var(--glass-film-subtle)",
      WebkitBackdropFilter: "var(--glass-film-subtle)",
      fontSize: "var(--text-xs)",
      lineHeight: 1.4,
      borderRadius: "var(--radius-sm)",
      boxShadow: "var(--shadow-md)",
      pointerEvents: "none"
    }
  }, label) : null);
}
Object.assign(__ds_scope, { Tooltip });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/feedback/Tooltip.jsx", error: String((e && e.message) || e) }); }

// components/forms/Checkbox.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
function Checkbox({
  label,
  description,
  checked,
  indeterminate = false,
  disabled = false,
  onChange,
  id,
  style,
  ...rest
}) {
  const [focus, setFocus] = React.useState(false);
  const ref = React.useRef(null);
  React.useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate;
  }, [indeterminate]);
  const on = checked || indeterminate;
  return /*#__PURE__*/React.createElement("label", {
    style: {
      display: "inline-flex",
      alignItems: "flex-start",
      gap: "var(--space-2)",
      cursor: disabled ? "not-allowed" : "pointer",
      minWidth: 0,
      color: disabled ? "var(--text-subtle)" : "var(--text-body)",
      ...style
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      position: "relative",
      display: "inline-flex",
      flex: "0 0 auto",
      marginTop: 1
    }
  }, /*#__PURE__*/React.createElement("input", _extends({
    ref: ref,
    id: id,
    type: "checkbox",
    checked: !!checked,
    disabled: disabled,
    onChange: onChange,
    onFocus: () => setFocus(true),
    onBlur: () => setFocus(false),
    style: {
      position: "absolute",
      opacity: 0,
      width: 16,
      height: 16,
      margin: 0,
      cursor: "inherit"
    }
  }, rest)), /*#__PURE__*/React.createElement("span", {
    "aria-hidden": "true",
    style: {
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      width: 16,
      height: 16,
      background: disabled ? "var(--surface-disabled)" : on ? "var(--navy-700)" : "var(--surface-input)",
      border: `var(--border-width) solid ${on && !disabled ? "var(--navy-700)" : "var(--border-input)"}`,
      borderRadius: "var(--radius-sm)",
      boxShadow: focus ? "var(--focus-ring)" : "none",
      transition: "var(--transition-control)"
    }
  }, indeterminate ? /*#__PURE__*/React.createElement("svg", {
    width: "10",
    height: "10",
    viewBox: "0 0 24 24",
    "aria-hidden": "true"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M5 12h14",
    stroke: "#fff",
    strokeWidth: "3.5",
    strokeLinecap: "round"
  })) : checked ? /*#__PURE__*/React.createElement("svg", {
    width: "11",
    height: "11",
    viewBox: "0 0 24 24",
    "aria-hidden": "true"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M4 12.5l5 5L20 6.5",
    fill: "none",
    stroke: "#fff",
    strokeWidth: "3.25",
    strokeLinecap: "round",
    strokeLinejoin: "round"
  })) : null)), label || description ? /*#__PURE__*/React.createElement("span", {
    style: {
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      display: "block",
      fontSize: "var(--text-base)",
      lineHeight: 1.35
    }
  }, label), description ? /*#__PURE__*/React.createElement("span", {
    style: {
      display: "block",
      marginTop: 2,
      fontSize: "var(--text-xs)",
      color: "var(--text-muted)"
    }
  }, description) : null) : null);
}
Object.assign(__ds_scope, { Checkbox });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Checkbox.jsx", error: String((e && e.message) || e) }); }

// components/forms/FileUpload.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/* Evidence upload for compliance submissions. Dashed drop zone + attached-file list.
   Cosmetic only — wire to the Django form's file field in production. */
function FileUpload({
  accept = "PDF, CSV, XLSX up to 25 MB",
  files = [],
  onRemove,
  disabled = false,
  style,
  ...rest
}) {
  const [over, setOver] = React.useState(false);
  return /*#__PURE__*/React.createElement("div", _extends({
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-2)",
      minWidth: 0,
      ...style
    }
  }, rest), /*#__PURE__*/React.createElement("div", {
    onDragOver: e => {
      e.preventDefault();
      setOver(true);
    },
    onDragLeave: () => setOver(false),
    onDrop: e => {
      e.preventDefault();
      setOver(false);
    },
    style: {
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      gap: "var(--space-2)",
      padding: "var(--space-6) var(--space-4)",
      textAlign: "center",
      background: over ? "var(--surface-selected)" : "var(--surface-sunken)",
      border: `1px dashed ${over ? "var(--border-focus)" : "var(--border-strong)"}`,
      borderRadius: "var(--radius-card)",
      transition: "var(--transition-control)",
      opacity: disabled ? 0.6 : 1
    }
  }, /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: "upload-cloud",
    size: 22,
    color: "var(--text-subtle)"
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: "var(--text-sm)",
      color: "var(--text-body)"
    }
  }, "Drag files here or ", /*#__PURE__*/React.createElement("span", {
    style: {
      color: "var(--text-link)",
      fontWeight: "var(--weight-medium)"
    }
  }, "browse")), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: "var(--text-xs)",
      color: "var(--text-muted)"
    }
  }, accept)), files.length ? /*#__PURE__*/React.createElement("ul", {
    style: {
      listStyle: "none",
      margin: 0,
      padding: 0,
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-1)"
    }
  }, files.map(f => /*#__PURE__*/React.createElement("li", {
    key: f.name,
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--space-2)",
      padding: "var(--space-15) var(--space-2)",
      background: "var(--surface-input)",
      border: "var(--border-width) solid var(--border-subtle)",
      borderRadius: "var(--radius-md)",
      fontSize: "var(--text-sm)"
    }
  }, /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: "paperclip",
    size: 14,
    color: "var(--text-subtle)"
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      flex: 1,
      minWidth: 0,
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap"
    }
  }, f.name), /*#__PURE__*/React.createElement("span", {
    style: {
      color: "var(--text-muted)",
      fontSize: "var(--text-xs)",
      fontVariantNumeric: "tabular-nums"
    }
  }, f.size), onRemove ? /*#__PURE__*/React.createElement(__ds_scope.Button, {
    variant: "ghost",
    size: "sm",
    onClick: () => onRemove(f)
  }, "Remove") : null))) : null);
}
Object.assign(__ds_scope, { FileUpload });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/FileUpload.jsx", error: String((e && e.message) || e) }); }

// components/forms/FormField.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/* Label-above-field wrapper. Owns the label, optional/required marker, hint and error.
   Every input in the portal is wrapped in one of these. */
function FormField({
  label,
  htmlFor,
  hint,
  error,
  required = false,
  optional = false,
  children,
  maxWidth = "var(--field-max)",
  style,
  ...rest
}) {
  return /*#__PURE__*/React.createElement("div", _extends({
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-15)",
      maxWidth,
      minWidth: 0,
      ...style
    }
  }, rest), label ? /*#__PURE__*/React.createElement("label", {
    htmlFor: htmlFor,
    style: {
      display: "flex",
      alignItems: "baseline",
      gap: "var(--space-1)",
      fontSize: "var(--type-label-size)",
      fontWeight: "var(--type-label-weight)",
      color: "var(--text-body)",
      lineHeight: 1.35
    }
  }, label, required ? /*#__PURE__*/React.createElement("span", {
    "aria-hidden": "true",
    style: {
      color: "var(--text-danger)"
    }
  }, "*") : null, optional && !required ? /*#__PURE__*/React.createElement("span", {
    style: {
      fontWeight: "var(--weight-regular)",
      fontSize: "var(--text-xs)",
      color: "var(--text-subtle)"
    }
  }, "(optional)") : null) : null, children, error ? /*#__PURE__*/React.createElement("p", {
    role: "alert",
    style: {
      margin: 0,
      fontSize: "var(--type-hint-size)",
      color: "var(--text-danger)",
      fontWeight: "var(--weight-medium)"
    }
  }, error) : hint ? /*#__PURE__*/React.createElement("p", {
    style: {
      margin: 0,
      fontSize: "var(--type-hint-size)",
      color: "var(--text-muted)"
    }
  }, hint) : null);
}
Object.assign(__ds_scope, { FormField });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/FormField.jsx", error: String((e && e.message) || e) }); }

// components/forms/Input.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
function Input({
  size = "md",
  invalid = false,
  disabled = false,
  iconLeft,
  suffix,
  prefix,
  type = "text",
  mono = false,
  style,
  ...rest
}) {
  const [focus, setFocus] = React.useState(false);
  const h = size === "sm" ? "var(--control-height-sm)" : size === "lg" ? "var(--control-height-lg)" : "var(--control-height)";
  const border = invalid ? "var(--border-danger)" : focus ? "var(--border-focus)" : "var(--border-input)";
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--space-2)",
      height: h,
      padding: "0 var(--pad-control-x)",
      background: disabled ? "var(--surface-disabled)" : "var(--surface-input)",
      backdropFilter: "var(--glass-film-subtle)",
      WebkitBackdropFilter: "var(--glass-film-subtle)",
      border: `var(--border-width) solid ${border}`,
      borderRadius: "var(--radius-control)",
      boxShadow: focus ? invalid ? "var(--focus-ring-danger)" : "var(--focus-ring)" : "none",
      transition: "var(--transition-control)",
      minWidth: 0,
      ...style
    }
  }, iconLeft ? /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: iconLeft,
    size: 14,
    color: "var(--text-subtle)"
  }) : null, prefix ? /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: "var(--text-sm)",
      color: "var(--text-muted)",
      whiteSpace: "nowrap"
    }
  }, prefix) : null, /*#__PURE__*/React.createElement("input", _extends({
    type: type,
    disabled: disabled,
    "aria-invalid": invalid || undefined,
    onFocus: () => setFocus(true),
    onBlur: () => setFocus(false),
    style: {
      flex: 1,
      minWidth: 0,
      height: "100%",
      padding: 0,
      margin: 0,
      border: 0,
      outline: "none",
      background: "transparent",
      fontFamily: mono ? "var(--font-mono)" : "var(--font-sans)",
      fontSize: size === "sm" ? "var(--text-sm)" : "var(--text-base)",
      color: disabled ? "var(--text-subtle)" : "var(--text-body)",
      fontVariantNumeric: mono ? "tabular-nums" : undefined
    }
  }, rest)), suffix ? /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: "var(--text-sm)",
      color: "var(--text-muted)",
      whiteSpace: "nowrap"
    }
  }, suffix) : null);
}
Object.assign(__ds_scope, { Input });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Input.jsx", error: String((e && e.message) || e) }); }

// components/forms/Radio.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
function Radio({
  label,
  description,
  checked,
  disabled = false,
  onChange,
  name,
  value,
  id,
  style,
  ...rest
}) {
  const [focus, setFocus] = React.useState(false);
  return /*#__PURE__*/React.createElement("label", {
    style: {
      display: "inline-flex",
      alignItems: "flex-start",
      gap: "var(--space-2)",
      cursor: disabled ? "not-allowed" : "pointer",
      minWidth: 0,
      color: disabled ? "var(--text-subtle)" : "var(--text-body)",
      ...style
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      position: "relative",
      display: "inline-flex",
      flex: "0 0 auto",
      marginTop: 1
    }
  }, /*#__PURE__*/React.createElement("input", _extends({
    id: id,
    type: "radio",
    name: name,
    value: value,
    checked: !!checked,
    disabled: disabled,
    onChange: onChange,
    onFocus: () => setFocus(true),
    onBlur: () => setFocus(false),
    style: {
      position: "absolute",
      opacity: 0,
      width: 16,
      height: 16,
      margin: 0,
      cursor: "inherit"
    }
  }, rest)), /*#__PURE__*/React.createElement("span", {
    "aria-hidden": "true",
    style: {
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      width: 16,
      height: 16,
      borderRadius: "var(--radius-pill)",
      background: disabled ? "var(--surface-disabled)" : "var(--surface-input)",
      border: `${checked && !disabled ? "5px" : "1px"} solid ${checked && !disabled ? "var(--navy-700)" : "var(--border-input)"}`,
      boxShadow: focus ? "var(--focus-ring)" : "none",
      transition: "var(--transition-control)"
    }
  })), label || description ? /*#__PURE__*/React.createElement("span", {
    style: {
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      display: "block",
      fontSize: "var(--text-base)",
      lineHeight: 1.35
    }
  }, label), description ? /*#__PURE__*/React.createElement("span", {
    style: {
      display: "block",
      marginTop: 2,
      fontSize: "var(--text-xs)",
      color: "var(--text-muted)"
    }
  }, description) : null) : null);
}
Object.assign(__ds_scope, { Radio });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Radio.jsx", error: String((e && e.message) || e) }); }

// components/forms/Select.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/* Native select with the portal's control chrome and a drawn chevron. */
function Select({
  options = [],
  placeholder,
  size = "md",
  invalid = false,
  disabled = false,
  style,
  ...rest
}) {
  const [focus, setFocus] = React.useState(false);
  const h = size === "sm" ? "var(--control-height-sm)" : size === "lg" ? "var(--control-height-lg)" : "var(--control-height)";
  const border = invalid ? "var(--border-danger)" : focus ? "var(--border-focus)" : "var(--border-input)";
  return /*#__PURE__*/React.createElement("div", {
    style: {
      position: "relative",
      minWidth: 0,
      ...style
    }
  }, /*#__PURE__*/React.createElement("select", _extends({
    disabled: disabled,
    "aria-invalid": invalid || undefined,
    onFocus: () => setFocus(true),
    onBlur: () => setFocus(false),
    style: {
      width: "100%",
      height: h,
      padding: "0 30px 0 var(--pad-control-x)",
      fontFamily: "var(--font-sans)",
      fontSize: size === "sm" ? "var(--text-sm)" : "var(--text-base)",
      color: disabled ? "var(--text-subtle)" : "var(--text-body)",
      background: disabled ? "var(--surface-disabled)" : "var(--surface-input)",
      backdropFilter: "var(--glass-film-subtle)",
      WebkitBackdropFilter: "var(--glass-film-subtle)",
      border: `var(--border-width) solid ${border}`,
      borderRadius: "var(--radius-control)",
      boxShadow: focus ? invalid ? "var(--focus-ring-danger)" : "var(--focus-ring)" : "none",
      appearance: "none",
      WebkitAppearance: "none",
      outline: "none",
      cursor: disabled ? "not-allowed" : "pointer",
      transition: "var(--transition-control)"
    }
  }, rest), placeholder ? /*#__PURE__*/React.createElement("option", {
    value: ""
  }, placeholder) : null, options.map(o => {
    const value = typeof o === "string" ? o : o.value;
    const label = typeof o === "string" ? o : o.label;
    return /*#__PURE__*/React.createElement("option", {
      key: value,
      value: value
    }, label);
  })), /*#__PURE__*/React.createElement("svg", {
    width: "12",
    height: "12",
    viewBox: "0 0 24 24",
    "aria-hidden": "true",
    style: {
      position: "absolute",
      right: 10,
      top: "50%",
      marginTop: -6,
      pointerEvents: "none"
    }
  }, /*#__PURE__*/React.createElement("path", {
    d: "M6 9l6 6 6-6",
    fill: "none",
    stroke: "var(--text-muted)",
    strokeWidth: "2.25",
    strokeLinecap: "round",
    strokeLinejoin: "round"
  })));
}
Object.assign(__ds_scope, { Select });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Select.jsx", error: String((e && e.message) || e) }); }

// components/forms/Switch.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/* Switch = a setting that takes effect immediately (notification toggles, feature flags).
   For form values that get submitted with a POST, use Checkbox. */
function Switch({
  label,
  description,
  checked,
  disabled = false,
  onChange,
  id,
  style,
  ...rest
}) {
  const [focus, setFocus] = React.useState(false);
  return /*#__PURE__*/React.createElement("label", {
    style: {
      display: "inline-flex",
      alignItems: "center",
      gap: "var(--space-3)",
      cursor: disabled ? "not-allowed" : "pointer",
      minWidth: 0,
      color: disabled ? "var(--text-subtle)" : "var(--text-body)",
      ...style
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      position: "relative",
      display: "inline-flex",
      flex: "0 0 auto"
    }
  }, /*#__PURE__*/React.createElement("input", _extends({
    id: id,
    type: "checkbox",
    role: "switch",
    checked: !!checked,
    disabled: disabled,
    onChange: onChange,
    onFocus: () => setFocus(true),
    onBlur: () => setFocus(false),
    style: {
      position: "absolute",
      opacity: 0,
      width: 34,
      height: 20,
      margin: 0,
      cursor: "inherit"
    }
  }, rest)), /*#__PURE__*/React.createElement("span", {
    "aria-hidden": "true",
    style: {
      display: "inline-flex",
      alignItems: "center",
      width: 34,
      height: 20,
      padding: 2,
      background: disabled ? "var(--gray-200)" : checked ? "var(--navy-700)" : "var(--gray-300)",
      borderRadius: "var(--radius-pill)",
      boxShadow: focus ? "var(--focus-ring)" : "none",
      transition: "var(--transition-control)"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      width: 16,
      height: 16,
      borderRadius: "var(--radius-pill)",
      background: "#fff",
      boxShadow: "var(--shadow-xs)",
      transform: checked ? "translateX(14px)" : "translateX(0)",
      transition: `transform var(--duration-fast) var(--ease-standard)`
    }
  }))), label || description ? /*#__PURE__*/React.createElement("span", {
    style: {
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      display: "block",
      fontSize: "var(--text-base)",
      lineHeight: 1.35
    }
  }, label), description ? /*#__PURE__*/React.createElement("span", {
    style: {
      display: "block",
      marginTop: 2,
      fontSize: "var(--text-xs)",
      color: "var(--text-muted)"
    }
  }, description) : null) : null);
}
Object.assign(__ds_scope, { Switch });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Switch.jsx", error: String((e && e.message) || e) }); }

// components/forms/Textarea.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
function Textarea({
  rows = 4,
  invalid = false,
  disabled = false,
  mono = false,
  style,
  ...rest
}) {
  const [focus, setFocus] = React.useState(false);
  const border = invalid ? "var(--border-danger)" : focus ? "var(--border-focus)" : "var(--border-input)";
  return /*#__PURE__*/React.createElement("textarea", _extends({
    rows: rows,
    disabled: disabled,
    "aria-invalid": invalid || undefined,
    onFocus: () => setFocus(true),
    onBlur: () => setFocus(false),
    style: {
      width: "100%",
      minWidth: 0,
      resize: "vertical",
      padding: "8px var(--pad-control-x)",
      fontFamily: mono ? "var(--font-mono)" : "var(--font-sans)",
      fontSize: "var(--text-base)",
      lineHeight: "var(--leading-normal)",
      color: disabled ? "var(--text-subtle)" : "var(--text-body)",
      background: disabled ? "var(--surface-disabled)" : "var(--surface-input)",
      backdropFilter: "var(--glass-film-subtle)",
      WebkitBackdropFilter: "var(--glass-film-subtle)",
      border: `var(--border-width) solid ${border}`,
      borderRadius: "var(--radius-control)",
      boxShadow: focus ? invalid ? "var(--focus-ring-danger)" : "var(--focus-ring)" : "none",
      outline: "none",
      transition: "var(--transition-control)",
      ...style
    }
  }, rest));
}
Object.assign(__ds_scope, { Textarea });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Textarea.jsx", error: String((e && e.message) || e) }); }

// components/navigation/Breadcrumbs.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
function Breadcrumbs({
  items = [],
  onNavigate,
  style,
  ...rest
}) {
  return /*#__PURE__*/React.createElement("nav", _extends({
    "aria-label": "Breadcrumb",
    style: {
      minWidth: 0,
      ...style
    }
  }, rest), /*#__PURE__*/React.createElement("ol", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--space-1)",
      listStyle: "none",
      margin: 0,
      padding: 0,
      flexWrap: "wrap"
    }
  }, items.map((item, i) => {
    const last = i === items.length - 1;
    return /*#__PURE__*/React.createElement("li", {
      key: item.label + i,
      style: {
        display: "flex",
        alignItems: "center",
        gap: "var(--space-1)",
        minWidth: 0
      }
    }, last ? /*#__PURE__*/React.createElement("span", {
      "aria-current": "page",
      style: {
        fontSize: "var(--text-xs)",
        color: "var(--text-muted)",
        fontWeight: "var(--weight-medium)"
      }
    }, item.label) : /*#__PURE__*/React.createElement("a", {
      href: item.href || "#",
      onClick: e => {
        e.preventDefault();
        onNavigate && onNavigate(item.id || item.label);
      },
      style: {
        fontSize: "var(--text-xs)",
        color: "var(--text-link)",
        textDecoration: "none",
        borderBottom: 0
      }
    }, item.label), !last ? /*#__PURE__*/React.createElement(__ds_scope.Icon, {
      name: "chevron-right",
      size: 12,
      color: "var(--text-subtle)"
    }) : null);
  })));
}
Object.assign(__ds_scope, { Breadcrumbs });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/navigation/Breadcrumbs.jsx", error: String((e && e.message) || e) }); }

// components/navigation/Pagination.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/* Table footer pagination: range readout, page size, prev/next. */
function Pagination({
  page = 1,
  pageSize = 25,
  total = 0,
  pageSizes = [25, 50, 100],
  onPageChange,
  onPageSizeChange,
  style,
  ...rest
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  return /*#__PURE__*/React.createElement("div", _extends({
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--space-4)",
      flexWrap: "wrap",
      padding: "var(--space-2) var(--pad-cell-x)",
      borderTop: "var(--border-width) solid var(--border-subtle)",
      fontSize: "var(--text-sm)",
      color: "var(--text-muted)",
      ...style
    }
  }, rest), /*#__PURE__*/React.createElement("span", {
    style: {
      fontVariantNumeric: "tabular-nums"
    }
  }, from.toLocaleString(), "\u2013", to.toLocaleString(), " of ", total.toLocaleString()), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--space-2)"
    }
  }, /*#__PURE__*/React.createElement("span", null, "Rows"), /*#__PURE__*/React.createElement(__ds_scope.Select, {
    size: "sm",
    value: String(pageSize),
    options: pageSizes.map(n => String(n)),
    onChange: e => onPageSizeChange && onPageSizeChange(Number(e.target.value)),
    style: {
      width: 72
    }
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--space-1)"
    }
  }, /*#__PURE__*/React.createElement(PageBtn, {
    icon: "chevron-left",
    label: "Previous page",
    disabled: page <= 1,
    onClick: () => onPageChange && onPageChange(page - 1)
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      padding: "0 var(--space-2)",
      fontVariantNumeric: "tabular-nums",
      color: "var(--text-body)"
    }
  }, "Page ", page, " of ", pages), /*#__PURE__*/React.createElement(PageBtn, {
    icon: "chevron-right",
    label: "Next page",
    disabled: page >= pages,
    onClick: () => onPageChange && onPageChange(page + 1)
  })));
}
function PageBtn({
  icon,
  label,
  disabled,
  onClick
}) {
  const [hover, setHover] = React.useState(false);
  return /*#__PURE__*/React.createElement("button", {
    type: "button",
    "aria-label": label,
    disabled: disabled,
    onClick: onClick,
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      width: 28,
      height: 28,
      padding: 0,
      color: disabled ? "var(--text-subtle)" : "var(--text-body)",
      background: hover && !disabled ? "var(--action-secondary-bg-hover)" : "var(--action-secondary-bg)",
      border: "var(--border-width) solid var(--border-input)",
      borderRadius: "var(--radius-control)",
      cursor: disabled ? "not-allowed" : "pointer",
      transition: "var(--transition-control)"
    }
  }, /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: icon,
    size: 14
  }));
}
Object.assign(__ds_scope, { Pagination });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/navigation/Pagination.jsx", error: String((e && e.message) || e) }); }

// components/data/DataTable.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/* The portal's table. Frosted header, 1px row rules, 40px rows, optional selection.

   Sorting, filtering, search and pagination are BUILT IN and work with no wiring — this
   replaces what the old DataTables plugin did.
   `<DataTable columns={...} rows={...} search filterable paginated />` gives you a search box
   across every column, a per-column filter row, click-to-sort headers and a page footer.

   Sort is type-aware: numbers and dates compare as values, everything else compares as a
   locale string with numeric collation, so CKT-9 sorts before CKT-40182.

   Both can still be driven from outside when the server does the work — pass `sortKey` +
   `onSort` to control sorting, or `onFiltersChange` to take over filtering. Set
   `serverSide` when rows arrive already sorted and filtered and the component should only
   render the controls.

   Column: { key, header, width, align, mono, render(row), sortAccessor(row),
             filter: "text" | "select" | false } */
function DataTable({
  columns = [],
  rows = [],
  selectable = false,
  selected = [],
  onSelectedChange,
  sortKey,
  sortDir,
  onSort,
  defaultSortKey,
  defaultSortDir = "asc",
  search = false,
  searchPlaceholder = "Filter records",
  filterable = false,
  filters,
  onFiltersChange,
  serverSide = false,
  paginated = false,
  pageSize: pageSizeProp,
  pageSizeOptions,
  page: pageProp,
  onPageChange,
  total,
  stickyHeader = false,
  onRowClick,
  compact = false,
  emptyState,
  toolbarActions,
  style,
  ...rest
}) {
  const sortControlled = sortKey !== undefined;
  const [selfSort, setSelfSort] = React.useState({
    key: defaultSortKey,
    dir: defaultSortDir
  });
  const activeSortKey = sortControlled ? sortKey : selfSort.key;
  const activeSortDir = (sortControlled ? sortDir : selfSort.dir) || "asc";
  const filtersControlled = filters !== undefined;
  const [selfFilters, setSelfFilters] = React.useState({});
  const activeFilters = filtersControlled ? filters : selfFilters;
  const [query, setQuery] = React.useState("");
  const [filterRowOpen, setFilterRowOpen] = React.useState(filterable === "open");
  const handleSort = key => {
    if (onSort) onSort(key);
    if (!sortControlled) {
      setSelfSort(s => s.key === key ? {
        key,
        dir: s.dir === "asc" ? "desc" : "asc"
      } : {
        key,
        dir: "asc"
      });
    }
  };
  const setFilter = (key, value) => {
    const next = {
      ...activeFilters
    };
    if (value === "" || value == null) delete next[key];else next[key] = value;
    if (!filtersControlled) setSelfFilters(next);
    onFiltersChange && onFiltersChange(next);
  };
  const clearAll = () => {
    setQuery("");
    if (!filtersControlled) setSelfFilters({});
    onFiltersChange && onFiltersChange({});
  };

  /* Unique values per select-filter column, taken from the unfiltered rows so the options
     don't disappear as you narrow the set. */
  const selectOptions = React.useMemo(() => {
    const out = {};
    columns.forEach(c => {
      if (c.filter !== "select") return;
      out[c.key] = Array.from(new Set(rows.map(r => cellText(r, c)).filter(Boolean))).sort((a, b) => a.localeCompare(b, undefined, {
        numeric: true
      }));
    });
    return out;
  }, [columns, rows]);
  const view = React.useMemo(() => {
    if (serverSide) return rows;
    let out = rows;
    const q = query.trim().toLowerCase();
    if (q) out = out.filter(r => columns.some(c => cellText(r, c).toLowerCase().includes(q)));
    Object.entries(activeFilters).forEach(([key, value]) => {
      const col = columns.find(c => c.key === key);
      if (!col || !value) return;
      const v = String(value).toLowerCase();
      out = out.filter(r => col.filter === "select" ? cellText(r, col).toLowerCase() === v : cellText(r, col).toLowerCase().includes(v));
    });
    if (activeSortKey) {
      const col = columns.find(c => c.key === activeSortKey);
      const dir = activeSortDir === "desc" ? -1 : 1;
      out = [...out].sort((a, b) => dir * compareRows(a, b, col, activeSortKey));
    }
    return out;
  }, [rows, columns, query, activeFilters, activeSortKey, activeSortDir, serverSide]);
  const filterCount = Object.keys(activeFilters).length + (query.trim() ? 1 : 0);

  /* Pagination. Uncontrolled unless `page` is passed; the page resets whenever the filters
     or the sort change, because page 4 of a different result set means nothing. */
  const pageControlled = pageProp !== undefined;
  const [selfPage, setSelfPage] = React.useState(1);
  const [selfPageSize, setSelfPageSize] = React.useState(pageSizeProp || 25);
  const pageSize = selfPageSize;
  const page = pageControlled ? pageProp : selfPage;
  React.useEffect(() => {
    if (!pageControlled) setSelfPage(1);
  }, [query, activeFilters, activeSortKey, activeSortDir, pageSize]);
  const goToPage = p => {
    if (!pageControlled) setSelfPage(p);
    onPageChange && onPageChange(p);
  };
  const rowCount = total != null ? total : view.length;
  const paged = React.useMemo(() => paginated && !serverSide ? view.slice((page - 1) * pageSize, page * pageSize) : view, [paginated, serverSide, view, page, pageSize]);
  const hasFilterRow = filterable && filterRowOpen;
  const allOn = paged.length > 0 && paged.every(r => selected.includes(r.id));
  const someOn = selected.length > 0 && !allOn;
  const rowH = compact ? "var(--row-height-compact)" : "var(--row-height)";
  const padY = compact ? "5px" : "var(--pad-cell-y)";
  const toggleAll = () => onSelectedChange && onSelectedChange(allOn ? selected.filter(id => !paged.some(r => r.id === id)) : Array.from(new Set([...selected, ...paged.map(r => r.id)])));
  const toggleOne = id => onSelectedChange && onSelectedChange(selected.includes(id) ? selected.filter(s => s !== id) : [...selected, id]);
  const showToolbar = search || filterable || toolbarActions || filterCount > 0;
  return /*#__PURE__*/React.createElement("div", _extends({
    style: {
      width: "100%",
      minWidth: 0,
      ...style
    }
  }, rest), showToolbar ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--gap-inline)",
      flexWrap: "wrap",
      padding: "var(--space-2) var(--pad-cell-x)",
      borderBottom: "var(--border-width) solid var(--border-subtle)"
    }
  }, search ? /*#__PURE__*/React.createElement("label", {
    style: {
      position: "relative",
      display: "flex",
      alignItems: "center",
      flex: "0 1 280px",
      minWidth: 180
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      position: "absolute",
      left: 9,
      display: "flex",
      color: "var(--text-subtle)",
      pointerEvents: "none"
    }
  }, /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: "search",
    size: 14
  })), /*#__PURE__*/React.createElement("input", {
    value: query,
    onChange: e => setQuery(e.target.value),
    placeholder: searchPlaceholder,
    "aria-label": searchPlaceholder,
    style: {
      ...fieldStyle,
      height: 30,
      paddingLeft: 28
    }
  })) : null, filterable ? /*#__PURE__*/React.createElement(ToolbarButton, {
    icon: "sliders-horizontal",
    label: filterRowOpen ? "Hide column filters" : "Column filters",
    active: filterRowOpen,
    onClick: () => setFilterRowOpen(o => !o)
  }) : null, filterCount > 0 ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: "var(--text-xs)",
      color: "var(--text-muted)",
      fontVariantNumeric: "tabular-nums"
    }
  }, view.length.toLocaleString(), " of ", rows.length.toLocaleString()), /*#__PURE__*/React.createElement(ToolbarButton, {
    icon: "x",
    label: "Clear filters",
    onClick: clearAll
  })) : null, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1
    }
  }), toolbarActions) : null, !view.length && emptyState ? emptyState :
  /*#__PURE__*/
  /* A horizontal scrollport and a sticky header are mutually exclusive: an element with
     overflow-x:auto is a scroll container on BOTH axes, so the header would pin to a box
     that never scrolls. stickyHeader gives up horizontal scroll to get it. */
  React.createElement("div", {
    style: {
      width: "100%",
      overflowX: stickyHeader ? "visible" : "auto"
    }
  }, /*#__PURE__*/React.createElement("table", {
    style: {
      width: "100%",
      borderCollapse: "collapse",
      fontSize: "var(--text-base)"
    }
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, selectable ? /*#__PURE__*/React.createElement("th", {
    style: {
      ...thStyle,
      ...(stickyHeader ? stickyTh : staticTh),
      width: 36,
      paddingRight: 0
    }
  }, /*#__PURE__*/React.createElement(__ds_scope.Checkbox, {
    checked: allOn,
    indeterminate: someOn,
    onChange: toggleAll
  })) : null, columns.map(c => {
    const sorted = activeSortKey === c.key;
    const canSort = c.sortable !== false;
    return /*#__PURE__*/React.createElement(SortHeader, {
      key: c.key,
      column: c,
      sorted: sorted,
      dir: activeSortDir,
      canSort: canSort,
      onSort: () => canSort && handleSort(c.key),
      sticky: stickyHeader
    });
  })), hasFilterRow ? /*#__PURE__*/React.createElement("tr", null, selectable ? /*#__PURE__*/React.createElement("th", {
    style: {
      ...filterCellStyle,
      ...(stickyHeader ? stickyFilterTh : staticTh)
    }
  }) : null, columns.map(c => /*#__PURE__*/React.createElement("th", {
    key: c.key,
    style: {
      ...filterCellStyle,
      ...(stickyHeader ? stickyFilterTh : staticTh)
    }
  }, c.filter === false ? null : c.filter === "select" ? /*#__PURE__*/React.createElement("select", {
    value: activeFilters[c.key] || "",
    onChange: e => setFilter(c.key, e.target.value),
    "aria-label": `Filter by ${typeof c.header === "string" ? c.header : c.key}`,
    style: {
      ...fieldStyle,
      height: 26,
      paddingLeft: 7,
      paddingRight: 20
    }
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "All"), (selectOptions[c.key] || []).map(o => /*#__PURE__*/React.createElement("option", {
    key: o,
    value: o
  }, o))) : /*#__PURE__*/React.createElement("input", {
    value: activeFilters[c.key] || "",
    onChange: e => setFilter(c.key, e.target.value),
    placeholder: "\u2014",
    "aria-label": `Filter by ${typeof c.header === "string" ? c.header : c.key}`,
    style: {
      ...fieldStyle,
      height: 26,
      textAlign: c.align === "right" ? "right" : "left"
    }
  })))) : null), /*#__PURE__*/React.createElement("tbody", null, paged.map(row => /*#__PURE__*/React.createElement(Row, {
    key: row.id,
    row: row,
    columns: columns,
    rowH: rowH,
    padY: padY,
    selectable: selectable,
    checked: selected.includes(row.id),
    onToggle: toggleOne,
    onRowClick: onRowClick
  })), !view.length ? /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("td", {
    colSpan: columns.length + (selectable ? 1 : 0),
    style: {
      padding: "var(--space-8) var(--pad-cell-x)",
      textAlign: "center",
      color: "var(--text-muted)",
      fontSize: "var(--text-sm)"
    }
  }, "No records match these filters.")) : null))), paginated && view.length ? /*#__PURE__*/React.createElement(__ds_scope.Pagination, {
    page: page,
    pageSize: pageSize,
    total: rowCount,
    pageSizes: pageSizeOptions || undefined,
    onPageChange: goToPage,
    onPageSizeChange: n => {
      setSelfPageSize(n);
      goToPage(1);
    }
  }) : null);
}

/* Plain text for a cell, for searching and sorting. Uses sortAccessor, then the raw field,
   and only falls back to render() output when the value lives entirely in the renderer. */
function cellText(row, col) {
  if (col.sortAccessor) return String(col.sortAccessor(row) ?? "");
  const raw = row[col.key];
  if (raw != null && typeof raw !== "object") return String(raw);
  if (col.render) {
    const node = col.render(row);
    return typeof node === "string" || typeof node === "number" ? String(node) : flattenNode(node);
  }
  return "";
}
function flattenNode(node) {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(flattenNode).join(" ");
  const p = node.props;
  if (!p) return "";
  return flattenNode(p.children) || String(p.status ?? p.label ?? p.value ?? "");
}
function compareRows(a, b, col, key) {
  const av = col && col.sortAccessor ? col.sortAccessor(a) : a[key];
  const bv = col && col.sortAccessor ? col.sortAccessor(b) : b[key];
  if (av == null && bv == null) return 0;
  if (av == null) return 1;
  if (bv == null) return -1;
  if (typeof av === "number" && typeof bv === "number") return av - bv;
  if (av instanceof Date && bv instanceof Date) return av - bv;
  const as = cellText(a, col || {
    key
  });
  const bs = cellText(b, col || {
    key
  });
  const an = asNumber(as);
  const bn = asNumber(bs);
  if (an != null && bn != null) return an - bn;
  return as.localeCompare(bs, undefined, {
    numeric: true,
    sensitivity: "base"
  });
}

/* A formatted number, or null. Accepts an optional leading currency symbol and an optional
   trailing unit — "$11,900", "10,000 Mbps", "99.94%", "-0.6 pts" all compare as values.
   Anything else (an ID like CKT-40182, an IP, an ISO date) returns null and falls through to
   the string comparison, which already collates those correctly. */
function asNumber(value) {
  const m = String(value).trim().match(/^[^\w\s-]?\s*(-?[\d,]+(?:\.\d+)?)\s*(%|[a-z/]{1,6})?$/i);
  if (!m) return null;
  const n = parseFloat(m[1].replace(/,/g, ""));
  return Number.isNaN(n) ? null : n;
}
function SortHeader({
  column: c,
  sorted,
  dir,
  canSort,
  onSort,
  sticky
}) {
  const [hover, setHover] = React.useState(false);
  return /*#__PURE__*/React.createElement("th", {
    style: {
      ...thStyle,
      ...(sticky ? stickyTh : staticTh),
      width: c.width,
      textAlign: c.align || "left",
      cursor: canSort ? "pointer" : "default",
      color: sorted ? "var(--text-heading)" : "var(--text-muted)",
      background: hover && canSort ? "var(--glass-hover)" : "var(--surface-sunken)"
    },
    onClick: onSort,
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    "aria-sort": sorted ? dir === "asc" ? "ascending" : "descending" : canSort ? "none" : undefined
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      display: "inline-flex",
      alignItems: "center",
      gap: 4,
      justifyContent: c.align === "right" ? "flex-end" : "flex-start"
    }
  }, c.header, sorted ? /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: dir === "asc" ? "arrow-up" : "arrow-down",
    size: 11,
    strokeWidth: 2.5
  }) : canSort && hover ? /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: "chevrons-up-down",
    size: 11,
    strokeWidth: 2,
    color: "var(--text-subtle)"
  }) : null));
}
function ToolbarButton({
  icon,
  label,
  active,
  onClick
}) {
  const [hover, setHover] = React.useState(false);
  return /*#__PURE__*/React.createElement("button", {
    type: "button",
    onClick: onClick,
    title: label,
    "aria-label": label,
    "aria-pressed": active,
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      width: 30,
      height: 30,
      flex: "0 0 auto",
      cursor: "pointer",
      borderRadius: "var(--radius-control)",
      border: `var(--border-width) solid ${active ? "var(--border-strong)" : "transparent"}`,
      background: active ? "var(--action-secondary-bg-active)" : hover ? "var(--action-ghost-bg-hover)" : "transparent",
      color: active ? "var(--text-heading)" : "var(--text-muted)",
      transition: "var(--transition-control)"
    }
  }, /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: icon,
    size: 15
  }));
}
const thStyle = {
  padding: "8px var(--pad-cell-x)",
  background: "var(--surface-sunken)",
  backdropFilter: "var(--glass-film-subtle)",
  WebkitBackdropFilter: "var(--glass-film-subtle)",
  borderBottom: "var(--border-width) solid var(--border-default)",
  fontSize: "var(--type-table-header-size)",
  fontWeight: "var(--type-table-header-weight)",
  letterSpacing: "var(--type-table-header-tracking)",
  textTransform: "uppercase",
  color: "var(--text-muted)",
  whiteSpace: "nowrap",
  userSelect: "none"
};
const filterCellStyle = {
  padding: "5px var(--pad-cell-x)",
  background: "var(--surface-sunken)",
  borderBottom: "var(--border-width) solid var(--border-default)"
};
const staticTh = {
  position: "static"
};
const stickyTh = {
  position: "sticky",
  top: 0,
  zIndex: 2
};
const stickyFilterTh = {
  position: "sticky",
  top: "var(--row-height-compact)",
  zIndex: 2
};
const fieldStyle = {
  width: "100%",
  minWidth: 0,
  padding: "0 8px",
  background: "var(--surface-input)",
  border: "var(--border-width) solid var(--border-input)",
  borderRadius: "var(--radius-control)",
  color: "var(--text-body)",
  fontSize: "var(--text-xs)",
  fontFamily: "inherit",
  outline: "none"
};
function Row({
  row,
  columns,
  rowH,
  padY,
  selectable,
  checked,
  onToggle,
  onRowClick
}) {
  const [hover, setHover] = React.useState(false);
  return /*#__PURE__*/React.createElement("tr", {
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    onClick: onRowClick ? () => onRowClick(row) : undefined,
    style: {
      height: rowH,
      background: checked ? "var(--surface-selected)" : hover ? "var(--surface-hover)" : "transparent",
      cursor: onRowClick ? "pointer" : "default",
      transition: "var(--transition-surface)"
    }
  }, selectable ? /*#__PURE__*/React.createElement("td", {
    style: {
      ...tdStyle,
      padding: `${padY} 0 ${padY} var(--pad-cell-x)`,
      width: 36
    },
    onClick: e => e.stopPropagation()
  }, /*#__PURE__*/React.createElement(__ds_scope.Checkbox, {
    checked: checked,
    onChange: () => onToggle(row.id)
  })) : null, columns.map(c => /*#__PURE__*/React.createElement("td", {
    key: c.key,
    style: {
      ...tdStyle,
      padding: `${padY} var(--pad-cell-x)`,
      textAlign: c.align || "left",
      fontFamily: c.mono ? "var(--font-mono)" : "inherit",
      fontSize: c.mono ? "var(--type-data-size)" : "inherit",
      fontVariantNumeric: c.mono || c.align === "right" ? "tabular-nums" : undefined,
      whiteSpace: c.wrap ? "normal" : "nowrap",
      color: c.muted ? "var(--text-muted)" : "var(--text-body)"
    }
  }, c.render ? c.render(row) : row[c.key])));
}
const tdStyle = {
  padding: "var(--pad-cell-y) var(--pad-cell-x)",
  borderBottom: "var(--border-width) solid var(--border-subtle)",
  verticalAlign: "middle"
};
Object.assign(__ds_scope, { DataTable });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/DataTable.jsx", error: String((e && e.message) || e) }); }

// components/navigation/SidebarNav.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/* The portal's left rail, on frost.

   Three levels of structure: uppercase sections, items, and nested child items — e.g.
   Inventory → Global → Cisco. Nesting is recursive, so deeper is possible, but three levels
   is the practical limit before the indent runs out of rail. A group that contains the active
   item opens itself.

   Collapsing: the rail runs uncontrolled by default — pass `collapsible={false}` to remove
   the toggle, or drive it from outside with `collapsed` + `onCollapsedChange`. Collapsed, it
   shows top-level icons only at 56px, so give every top-level item an icon if you intend to
   let users collapse it; clicking a group there expands the rail rather than opening a
   submenu with nowhere to go. The chosen state persists per `storageKey`.

   `footer` may be a node or a function of ({ collapsed }) — use the function form when the
   footer has to shed parts at 56px. */
function SidebarNav({
  brand = "Nova",
  logo,
  logoCollapsed,
  sections = [],
  activeId,
  onNavigate,
  footer,
  collapsible = true,
  collapsed,
  onCollapsedChange,
  storageKey = "nova.nav.collapsed",
  style,
  ...rest
}) {
  const controlled = collapsed != null;
  const [selfCollapsed, setSelfCollapsed] = React.useState(() => {
    if (typeof window === "undefined" || !storageKey) return false;
    try {
      return window.localStorage.getItem(storageKey) === "1";
    } catch {
      return false;
    }
  });
  const isCollapsed = controlled ? collapsed : selfCollapsed;
  const setCollapsed = next => {
    if (!controlled) {
      setSelfCollapsed(next);
      if (storageKey) {
        try {
          window.localStorage.setItem(storageKey, next ? "1" : "0");
        } catch {}
      }
    }
    onCollapsedChange && onCollapsedChange(next);
  };
  const initial = {};
  sections.forEach(s => {
    initial[s.id] = s.defaultOpen !== false;
  });
  const [open, setOpen] = React.useState(initial);
  const toggleSection = id => setOpen(o => ({
    ...o,
    [id]: !o[id]
  }));
  return /*#__PURE__*/React.createElement("nav", _extends({
    "aria-label": "Primary",
    "data-collapsed": isCollapsed ? "" : undefined,
    style: {
      display: "flex",
      flexDirection: "column",
      width: isCollapsed ? "var(--nav-width-collapsed)" : "var(--nav-width)",
      flex: "0 0 auto",
      height: "100%",
      overflow: "hidden",
      background: "var(--surface-nav)",
      backdropFilter: "var(--glass-film-strong)",
      WebkitBackdropFilter: "var(--glass-film-strong)",
      borderRight: "var(--border-width) solid var(--glass-edge)",
      color: "var(--text-on-nav)",
      transition: "width var(--duration-slow) var(--ease-standard)",
      ...style
    }
  }, rest), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--space-2)",
      height: "var(--header-height)",
      flex: "0 0 auto",
      padding: isCollapsed ? "0 var(--space-2)" : "0 var(--space-3) 0 var(--space-4)",
      justifyContent: isCollapsed ? "center" : "flex-start",
      borderBottom: "var(--border-width) solid var(--border-nav)"
    }
  }, /*#__PURE__*/React.createElement(BrandMark, {
    brand: brand,
    logo: logo,
    logoCollapsed: logoCollapsed,
    collapsed: isCollapsed,
    collapsible: collapsible
  }), collapsible ? /*#__PURE__*/React.createElement(RailButton, {
    label: isCollapsed ? "Expand navigation" : "Collapse navigation",
    icon: isCollapsed ? "panel-left-open" : "panel-left-close",
    onClick: () => setCollapsed(!isCollapsed),
    expanded: !isCollapsed
  }) : null), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      overflowY: "auto",
      overflowX: "hidden",
      padding: "var(--space-3) 0"
    }
  }, sections.map((section, i) => /*#__PURE__*/React.createElement("div", {
    key: section.id,
    style: {
      marginBottom: "var(--space-1)"
    }
  }, section.label && !isCollapsed ? /*#__PURE__*/React.createElement("button", {
    type: "button",
    onClick: () => toggleSection(section.id),
    "aria-expanded": open[section.id] !== false,
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--space-2)",
      width: "100%",
      padding: "6px var(--space-4)",
      background: "transparent",
      border: 0,
      color: "var(--text-muted)",
      fontSize: "var(--text-2xs)",
      fontWeight: "var(--weight-semibold)",
      letterSpacing: "var(--tracking-caps)",
      textTransform: "uppercase",
      cursor: "pointer",
      textAlign: "left"
    }
  }, /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: open[section.id] !== false ? "chevron-down" : "chevron-right",
    size: 12,
    strokeWidth: 2.25
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      flex: 1
    }
  }, section.label)) : null, isCollapsed && i > 0 ? /*#__PURE__*/React.createElement("div", {
    style: {
      height: 1,
      margin: "var(--space-2) var(--space-3)",
      background: "var(--border-nav)"
    }
  }) : null, open[section.id] !== false || isCollapsed ? /*#__PURE__*/React.createElement(NavTree, {
    items: section.items || [],
    depth: 0,
    activeId: activeId,
    collapsed: isCollapsed,
    onNavigate: onNavigate,
    onExpandRail: () => setCollapsed(false)
  }) : null))), footer ? /*#__PURE__*/React.createElement("div", {
    style: {
      flex: "0 0 auto",
      padding: isCollapsed ? "var(--space-3) var(--space-2)" : "var(--space-3) var(--space-4)",
      borderTop: "var(--border-width) solid var(--border-nav)",
      display: "flex",
      justifyContent: isCollapsed ? "center" : "flex-start",
      overflow: "hidden"
    }
  }, typeof footer === "function" ? footer({
    collapsed: isCollapsed
  }) : footer) : null);
}

/* True when this item or anything under it is the active route — used to open the group it
   sits in and to mark that group's parent row. */
function containsActive(item, activeId) {
  if (!activeId) return false;
  if (item.id === activeId) return true;
  return (item.items || []).some(child => containsActive(child, activeId));
}
function NavTree({
  items,
  depth,
  activeId,
  collapsed,
  onNavigate,
  onExpandRail
}) {
  return items.map(item => (item.items || []).length ? /*#__PURE__*/React.createElement(NavGroup, {
    key: item.id,
    item: item,
    depth: depth,
    activeId: activeId,
    collapsed: collapsed,
    onNavigate: onNavigate,
    onExpandRail: onExpandRail
  }) : /*#__PURE__*/React.createElement(NavItem, {
    key: item.id,
    item: item,
    depth: depth,
    active: item.id === activeId,
    collapsed: collapsed,
    onNavigate: onNavigate
  }));
}
function NavGroup({
  item,
  depth,
  activeId,
  collapsed,
  onNavigate,
  onExpandRail
}) {
  const holdsActive = containsActive(item, activeId);
  const [open, setOpen] = React.useState(holdsActive || item.defaultOpen === true);
  const [hover, setHover] = React.useState(false);
  React.useEffect(() => {
    if (holdsActive) setOpen(true);
  }, [holdsActive]);

  /* At 56px there is no room for a submenu, so the disclosure becomes "give me the rail back". */
  const onClick = () => collapsed ? (onExpandRail(), setOpen(true)) : setOpen(o => !o);
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("button", {
    type: "button",
    onClick: onClick,
    "aria-expanded": collapsed ? undefined : open,
    title: collapsed ? item.label : undefined,
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      ...rowStyle(depth, collapsed),
      /* Matches the link rows: they reserve 3px on the left for the active marker, so
         without it the group's icon sits 3px further left than its children. */
      borderLeft: "var(--border-accent-width) solid transparent",
      paddingLeft: collapsed ? 0 : `calc(${indent(depth)} - var(--border-accent-width))`,
      borderTop: 0,
      borderRight: 0,
      borderBottom: 0,
      cursor: "pointer",
      textAlign: "left",
      color: holdsActive ? "var(--text-on-nav-active)" : "var(--text-on-nav)",
      background: hover ? "var(--surface-nav-hover)" : "transparent",
      fontWeight: holdsActive ? "var(--weight-medium)" : "var(--weight-regular)"
    }
  }, item.icon ? /*#__PURE__*/React.createElement(IconSlot, {
    name: item.icon,
    depth: depth
  }) : null, !collapsed ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("span", {
    style: {
      flex: 1,
      minWidth: 0,
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap"
    }
  }, item.label), /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: open ? "chevron-down" : "chevron-right",
    size: 13,
    strokeWidth: 2.25,
    color: "var(--text-subtle)"
  })) : null), open && !collapsed ? /*#__PURE__*/React.createElement(NavTree, {
    items: item.items,
    depth: depth + 1,
    activeId: activeId,
    collapsed: collapsed,
    onNavigate: onNavigate,
    onExpandRail: onExpandRail
  }) : null);
}
function NavItem({
  item,
  depth,
  active,
  collapsed,
  onNavigate
}) {
  const [hover, setHover] = React.useState(false);
  return /*#__PURE__*/React.createElement("a", {
    href: item.href || "#",
    "aria-current": active ? "page" : undefined,
    title: collapsed ? item.label : undefined,
    onClick: e => {
      e.preventDefault();
      onNavigate && onNavigate(item.id);
    },
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      ...rowStyle(depth, collapsed),
      textDecoration: "none",
      borderLeft: `var(--border-accent-width) solid ${active ? "var(--crimson-500)" : "transparent"}`,
      paddingLeft: collapsed ? 0 : `calc(${indent(depth)} - var(--border-accent-width))`,
      color: active ? "var(--text-on-nav-active)" : "var(--text-on-nav)",
      background: active ? "var(--surface-nav-active)" : hover ? "var(--surface-nav-hover)" : "transparent",
      boxShadow: active ? "var(--shadow-nav-active)" : "none",
      fontWeight: active ? "var(--weight-medium)" : "var(--weight-regular)",
      fontSize: depth > 0 ? "var(--text-sm)" : "var(--text-base)"
    }
  }, item.icon ? /*#__PURE__*/React.createElement(IconSlot, {
    name: item.icon,
    depth: depth
  }) : null, !collapsed ? /*#__PURE__*/React.createElement("span", {
    style: {
      flex: 1,
      minWidth: 0,
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap"
    }
  }, item.label) : null, !collapsed && item.count != null ? /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: "var(--text-2xs)",
      fontWeight: "var(--weight-semibold)",
      fontVariantNumeric: "tabular-nums",
      padding: "1px 5px",
      borderRadius: "var(--radius-sm)",
      background: item.countTone === "danger" ? "var(--crimson-500)" : "var(--neutral-tint)",
      color: item.countTone === "danger" ? "#fff" : "var(--neutral-ink)"
    }
  }, item.count) : null, collapsed && item.count != null ? /*#__PURE__*/React.createElement("span", {
    style: {
      position: "absolute",
      marginLeft: 14,
      marginTop: -12,
      width: 6,
      height: 6,
      borderRadius: "50%",
      background: item.countTone === "danger" ? "var(--crimson-500)" : "var(--navy-400)"
    }
  }) : null, !collapsed && item.external ? /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: "external-link",
    size: 12,
    color: "var(--text-subtle)"
  }) : null);
}

/* Every rail icon sits in a fixed 16px centred box. Lucide glyphs do not share a common ink
   box — a full-bleed circle like `globe` runs edge to edge where `cable` is inset — so
   dropping them straight into the flex row makes single icons look off-axis. The slot pins
   the axis regardless of which glyph is used. */
function IconSlot({
  name,
  depth
}) {
  return /*#__PURE__*/React.createElement("span", {
    style: {
      width: 16,
      flex: "0 0 16px",
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center"
    }
  }, /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: name,
    size: depth === 0 ? 16 : 14
  }));
}

/* Each level steps in 14px. Deeper rows are also a size smaller, so the hierarchy reads
   without needing connector lines. */
const indent = depth => `calc(var(--space-3) + ${depth * 14}px)`;
const rowStyle = (depth, collapsed) => ({
  display: "flex",
  alignItems: "center",
  gap: "var(--space-3)",
  height: depth > 0 ? 31 : 34,
  margin: "2px var(--space-2)",
  padding: collapsed ? 0 : `0 var(--space-3) 0 ${indent(depth)}`,
  justifyContent: collapsed ? "center" : "flex-start",
  borderRadius: "var(--radius-nav-item)",
  fontSize: depth > 0 ? "var(--text-sm)" : "var(--text-base)",
  transition: "var(--transition-control)"
});

/* The brand slot: a fixed 28px-tall box, so swapping the wordmark for an image does not
   shift the rail. `logo` takes an image URL or your own node; `logoCollapsed` is the mark
   used at 56px. With no logo it sets `brand` in type — no logo file was supplied with this
   system. Collapsed, the slot yields to the collapse toggle: 56px does not fit both. */
function BrandMark({
  brand,
  logo,
  logoCollapsed,
  collapsed,
  collapsible
}) {
  if (collapsed && collapsible) return null;
  const src = collapsed ? logoCollapsed || logo : logo;
  if (!src) {
    return collapsed ? /*#__PURE__*/React.createElement("span", {
      style: {
        fontSize: "var(--text-md)",
        fontWeight: "var(--weight-bold)",
        color: "var(--text-heading)"
      }
    }, brand.slice(0, 1)) : /*#__PURE__*/React.createElement("span", {
      style: {
        flex: 1,
        minWidth: 0,
        height: 28,
        display: "flex",
        alignItems: "center",
        fontSize: "var(--text-lg)",
        fontWeight: "var(--weight-bold)",
        letterSpacing: "-0.02em",
        color: "var(--text-heading)",
        whiteSpace: "nowrap",
        overflow: "hidden"
      }
    }, brand);
  }
  return /*#__PURE__*/React.createElement("span", {
    style: {
      flex: collapsed ? "0 0 auto" : 1,
      minWidth: 0,
      height: 28,
      display: "flex",
      alignItems: "center"
    }
  }, typeof src === "string" ? /*#__PURE__*/React.createElement("img", {
    src: src,
    alt: brand,
    style: {
      height: "100%",
      width: "auto",
      maxWidth: "100%",
      objectFit: "contain",
      objectPosition: "left center",
      display: "block"
    }
  }) : src);
}
function RailButton({
  label,
  icon,
  onClick,
  expanded
}) {
  const [hover, setHover] = React.useState(false);
  return /*#__PURE__*/React.createElement("button", {
    type: "button",
    onClick: onClick,
    "aria-label": label,
    title: label,
    "aria-expanded": expanded,
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      width: 28,
      height: 28,
      flex: "0 0 auto",
      padding: 0,
      cursor: "pointer",
      borderRadius: "var(--radius-control)",
      border: 0,
      background: hover ? "var(--action-ghost-bg-hover)" : "transparent",
      color: "var(--text-muted)",
      transition: "var(--transition-control)"
    }
  }, /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: icon,
    size: 16
  }));
}
Object.assign(__ds_scope, { SidebarNav });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/navigation/SidebarNav.jsx", error: String((e && e.message) || e) }); }

// components/navigation/Tabs.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/* Underline tabs for sub-views of one record (Overview / Config / Compliance / History). */
function Tabs({
  tabs = [],
  activeId,
  onChange,
  style,
  ...rest
}) {
  return /*#__PURE__*/React.createElement("div", _extends({
    role: "tablist",
    style: {
      display: "flex",
      alignItems: "stretch",
      gap: "var(--space-5)",
      borderBottom: "var(--border-width) solid var(--border-default)",
      minWidth: 0,
      overflowX: "auto",
      ...style
    }
  }, rest), tabs.map(t => /*#__PURE__*/React.createElement(Tab, {
    key: t.id,
    tab: t,
    active: t.id === activeId,
    onChange: onChange
  })));
}
function Tab({
  tab,
  active,
  onChange
}) {
  const [hover, setHover] = React.useState(false);
  return /*#__PURE__*/React.createElement("button", {
    type: "button",
    role: "tab",
    "aria-selected": active,
    disabled: tab.disabled,
    onClick: () => onChange && onChange(tab.id),
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      display: "inline-flex",
      alignItems: "center",
      gap: "var(--space-15)",
      padding: "0 0 9px",
      marginBottom: -1,
      background: "transparent",
      border: 0,
      borderBottom: `var(--border-width-thick) solid ${active ? "var(--navy-700)" : "transparent"}`,
      color: tab.disabled ? "var(--text-subtle)" : active ? "var(--text-heading)" : hover ? "var(--text-body)" : "var(--text-muted)",
      fontSize: "var(--text-base)",
      fontWeight: active ? "var(--weight-semibold)" : "var(--weight-medium)",
      whiteSpace: "nowrap",
      cursor: tab.disabled ? "not-allowed" : "pointer",
      transition: "var(--transition-control)",
      paddingTop: 9
    }
  }, tab.icon ? /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: tab.icon,
    size: 14
  }) : null, tab.label, tab.count != null ? /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: "var(--text-xs)",
      color: "var(--text-muted)",
      fontVariantNumeric: "tabular-nums"
    }
  }, tab.count) : null);
}
Object.assign(__ds_scope, { Tabs });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/navigation/Tabs.jsx", error: String((e && e.message) || e) }); }

// ui_kits/nova-portal/AppShell.jsx
try { (() => {
const {
  SidebarNav,
  Breadcrumbs,
  Icon,
  IconButton,
  Input,
  Button,
  Tooltip,
  Tabs
} = window.PortalDesignSystem_986bf0;

/* The authenticated chrome: collapsible left rail, frosted top bar, scrolling content column.
   The rail remembers its collapsed state itself — nothing here needs to hold it. */
function AppShell({
  activeId,
  onNavigate,
  theme,
  onToggleTheme,
  breadcrumbs,
  title,
  subtitle,
  actions,
  tabs,
  activeTab,
  onTabChange,
  children,
  toolbar
}) {
  /* Transparent: the lit field is fixed on <body> and must show through the chrome. */
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      height: "100%",
      minHeight: 0,
      background: "transparent"
    }
  }, /*#__PURE__*/React.createElement(SidebarNav, {
    brand: "Nova",
    sections: window.NovaData.navSections,
    activeId: activeId,
    onNavigate: onNavigate,
    footer: ({
      collapsed
    }) => /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        alignItems: "center",
        gap: "var(--space-2)",
        width: "100%",
        minWidth: 0
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: 26,
        height: 26,
        flex: "0 0 auto",
        background: "var(--navy-700)",
        borderRadius: "var(--radius-pill)",
        fontSize: "var(--text-2xs)",
        fontWeight: 600,
        color: "#fff"
      }
    }, "DO"), collapsed ? null : /*#__PURE__*/React.createElement("span", {
      style: {
        flex: 1,
        minWidth: 0,
        fontSize: "var(--text-xs)",
        color: "var(--text-body)",
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap"
      }
    }, "D. Okafor"), collapsed ? null : /*#__PURE__*/React.createElement(Tooltip, {
      label: theme === "dark" ? "Switch to light" : "Switch to dark",
      placement: "top"
    }, /*#__PURE__*/React.createElement("button", {
      type: "button",
      onClick: onToggleTheme,
      "aria-label": "Toggle theme",
      style: {
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: 26,
        height: 26,
        background: "transparent",
        color: "var(--text-muted)",
        border: "1px solid var(--border-input)",
        borderRadius: "var(--radius-control)",
        cursor: "pointer"
      }
    }, /*#__PURE__*/React.createElement(Icon, {
      name: theme === "dark" ? "sun" : "moon",
      size: 13
    }))))
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minWidth: 0,
      display: "flex",
      flexDirection: "column",
      position: "relative",
      zIndex: 0
    }
  }, /*#__PURE__*/React.createElement("header", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--space-4)",
      flex: "0 0 auto",
      height: "var(--header-height)",
      padding: "0 var(--page-gutter)",
      background: "var(--surface-header)",
      backdropFilter: "var(--glass-film-strong)",
      WebkitBackdropFilter: "var(--glass-film-strong)",
      borderBottom: "var(--border-width) solid var(--glass-edge)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: 300,
      maxWidth: "40%"
    }
  }, /*#__PURE__*/React.createElement(Input, {
    size: "sm",
    iconLeft: "search",
    placeholder: "Search assets, reports, tasks"
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1
    }
  }), /*#__PURE__*/React.createElement(Button, {
    variant: "ghost",
    size: "sm",
    iconLeft: "circle-help"
  }, "Help"), /*#__PURE__*/React.createElement(Tooltip, {
    label: "3 overdue reports",
    placement: "bottom"
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      position: "relative",
      display: "inline-flex"
    }
  }, /*#__PURE__*/React.createElement(IconButton, {
    icon: "bell",
    label: "Notifications"
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      position: "absolute",
      top: 5,
      right: 5,
      width: 7,
      height: 7,
      borderRadius: "var(--radius-pill)",
      background: "var(--crimson-500)",
      border: "1.5px solid var(--surface-header)"
    }
  })))), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minHeight: 0,
      overflowY: "auto"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: "100%",
      padding: "var(--page-gutter)",
      display: "flex",
      flexDirection: "column",
      gap: "var(--gap-section)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-3)"
    }
  }, breadcrumbs ? /*#__PURE__*/React.createElement(Breadcrumbs, {
    items: breadcrumbs,
    onNavigate: onNavigate
  }) : null, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "flex-end",
      gap: "var(--space-4)",
      flexWrap: "wrap"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("h1", {
    style: {
      fontSize: "var(--type-page-title-size)",
      fontWeight: "var(--type-page-title-weight)",
      letterSpacing: "var(--type-page-title-tracking)"
    }
  }, title), subtitle ? /*#__PURE__*/React.createElement("p", {
    style: {
      margin: "4px 0 0",
      fontSize: "var(--text-sm)",
      color: "var(--text-muted)"
    }
  }, subtitle) : null), actions ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--gap-inline)"
    }
  }, actions) : null), tabs ? /*#__PURE__*/React.createElement(Tabs, {
    tabs: tabs,
    activeId: activeTab,
    onChange: onTabChange
  }) : null, toolbar || null), children))));
}
Object.assign(window, {
  AppShell
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/nova-portal/AppShell.jsx", error: String((e && e.message) || e) }); }

// ui_kits/nova-portal/AuthAndSettingsScreens.jsx
try { (() => {
const {
  Button,
  FormField,
  Input,
  Checkbox,
  Alert,
  Icon,
  Card,
  Tabs,
  Switch,
  Select,
  Badge,
  DataTable,
  StatusPill,
  IconButton
} = window.PortalDesignSystem_986bf0;

/* Login: split layout. Left is the navy brand panel (name in type — no logo file exists).
   Right is the form. SSO first, credentials second, because most staff use SSO. */
function LoginScreen({
  onSignIn
}) {
  const [failed, setFailed] = React.useState(false);
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "minmax(0,1fr) 480px",
      height: "100%",
      background: "transparent"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      background: "var(--surface-brand-panel)",
      padding: "48px",
      display: "flex",
      flexDirection: "column",
      justifyContent: "space-between"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 40,
      fontWeight: 700,
      letterSpacing: "-0.03em",
      color: "#fff"
    }
  }, "Nova"), /*#__PURE__*/React.createElement("div", {
    style: {
      maxWidth: 460
    }
  }, /*#__PURE__*/React.createElement("h1", {
    style: {
      fontSize: 32,
      fontWeight: 600,
      letterSpacing: "-0.02em",
      color: "#fff",
      lineHeight: 1.2
    }
  }, "Network operations portal"), /*#__PURE__*/React.createElement("p", {
    style: {
      margin: "12px 0 0",
      fontSize: "var(--text-md)",
      color: "rgba(255,255,255,.82)",
      lineHeight: 1.6,
      textWrap: "pretty"
    }
  }, "Inventory, compliance reporting, task assignments and links to the rest of the toolchain."), /*#__PURE__*/React.createElement("ul", {
    style: {
      listStyle: "none",
      margin: "28px 0 0",
      padding: 0,
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-3)"
    }
  }, [["server", "4,182 managed assets"], ["shield-check", "6 reporting frameworks"], ["clock", "Records synced every 15 minutes"]].map(f => /*#__PURE__*/React.createElement("li", {
    key: f[1],
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--space-3)",
      color: "rgba(255,255,255,.78)",
      fontSize: "var(--text-sm)"
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: f[0],
    size: 15
  }), f[1])))), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: "var(--text-xs)",
      color: "rgba(255,255,255,.58)"
    }
  }, "Authorised use only. Activity is logged against your account.")), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      padding: "var(--space-8)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: "100%",
      maxWidth: 340,
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-5)"
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("h2", {
    style: {
      fontSize: "var(--text-xl)",
      fontWeight: 600
    }
  }, "Sign in"), /*#__PURE__*/React.createElement("p", {
    style: {
      margin: "4px 0 0",
      fontSize: "var(--text-sm)",
      color: "var(--text-muted)"
    }
  }, "Use your corporate account.")), failed ? /*#__PURE__*/React.createElement(Alert, {
    tone: "danger",
    title: "Sign-in failed"
  }, "Check your username and password, or use single sign-on.") : null, /*#__PURE__*/React.createElement(Button, {
    variant: "primary",
    size: "lg",
    fullWidth: true,
    iconLeft: "building-2",
    onClick: onSignIn
  }, "Continue with single sign-on"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--space-3)"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      flex: 1,
      height: 1,
      background: "var(--border-default)"
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: "var(--text-xs)",
      color: "var(--text-subtle)"
    }
  }, "or"), /*#__PURE__*/React.createElement("span", {
    style: {
      flex: 1,
      height: 1,
      background: "var(--border-default)"
    }
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--gap-field)"
    }
  }, /*#__PURE__*/React.createElement(FormField, {
    label: "Username",
    htmlFor: "un",
    maxWidth: "100%"
  }, /*#__PURE__*/React.createElement(Input, {
    id: "un",
    placeholder: "first.last",
    autoComplete: "username"
  })), /*#__PURE__*/React.createElement(FormField, {
    label: "Password",
    htmlFor: "pw",
    maxWidth: "100%"
  }, /*#__PURE__*/React.createElement(Input, {
    id: "pw",
    type: "password",
    placeholder: "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022",
    autoComplete: "current-password"
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      gap: "var(--space-3)"
    }
  }, /*#__PURE__*/React.createElement(Checkbox, {
    label: "Keep me signed in",
    defaultChecked: false
  }), /*#__PURE__*/React.createElement(Button, {
    variant: "link",
    size: "sm"
  }, "Forgot password")), /*#__PURE__*/React.createElement(Button, {
    size: "lg",
    fullWidth: true,
    onClick: () => setFailed(true)
  }, "Sign in")), /*#__PURE__*/React.createElement("p", {
    style: {
      margin: 0,
      fontSize: "var(--text-xs)",
      color: "var(--text-muted)",
      textWrap: "pretty"
    }
  }, "Access is granted through the network operations group. Contact the service desk if your role has changed."))));
}

/* Settings / admin: tabbed preferences and a role table. */
function SettingsScreen({
  theme,
  onToggleTheme
}) {
  const [tab, setTab] = React.useState("preferences");
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Tabs, {
    activeId: tab,
    onChange: setTab,
    tabs: [{
      id: "preferences",
      label: "Preferences"
    }, {
      id: "notifications",
      label: "Notifications"
    }, {
      id: "roles",
      label: "Roles & access",
      count: 5
    }, {
      id: "integrations",
      label: "Integrations"
    }]
  }), tab === "preferences" ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "minmax(0,1fr) 300px",
      gap: "var(--space-6)",
      alignItems: "start"
    }
  }, /*#__PURE__*/React.createElement(Card, {
    title: "Display",
    footer: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Button, null, "Reset"), /*#__PURE__*/React.createElement(Button, {
      variant: "primary"
    }, "Save preferences"))
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--gap-field)"
    }
  }, /*#__PURE__*/React.createElement(FormField, {
    label: "Theme",
    hint: "Dark theme uses the same tokens with re-pointed surfaces."
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      paddingTop: 2
    }
  }, /*#__PURE__*/React.createElement(Switch, {
    checked: theme === "dark",
    onChange: onToggleTheme,
    label: "Dark theme",
    description: "Applies immediately across the portal."
  }))), /*#__PURE__*/React.createElement(FormField, {
    label: "Default landing screen",
    htmlFor: "ls",
    maxWidth: 280
  }, /*#__PURE__*/React.createElement(Select, {
    id: "ls",
    defaultValue: "Dashboard",
    options: ["Dashboard", "Task assignments", "Circuits & WAN", "Compliance reports"]
  })), /*#__PURE__*/React.createElement(FormField, {
    label: "Table density",
    htmlFor: "td",
    maxWidth: 280,
    hint: "Compact fits roughly 30% more rows per screen."
  }, /*#__PURE__*/React.createElement(Select, {
    id: "td",
    defaultValue: "Comfortable",
    options: ["Comfortable", "Compact"]
  })), /*#__PURE__*/React.createElement(FormField, {
    label: "Timezone",
    htmlFor: "tz",
    maxWidth: 280
  }, /*#__PURE__*/React.createElement(Select, {
    id: "tz",
    defaultValue: "America/Chicago",
    options: ["UTC", "America/Chicago", "America/New_York", "America/Denver", "America/Los_Angeles"]
  })))), /*#__PURE__*/React.createElement(Card, {
    title: "Session"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-3)",
      fontSize: "var(--text-sm)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      justifyContent: "space-between",
      gap: "var(--space-3)"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      color: "var(--text-muted)"
    }
  }, "Signed in as"), /*#__PURE__*/React.createElement("span", null, "D. Okafor")), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      justifyContent: "space-between",
      gap: "var(--space-3)"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      color: "var(--text-muted)"
    }
  }, "Role"), /*#__PURE__*/React.createElement(Badge, {
    tone: "brand"
  }, "Network admin")), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      justifyContent: "space-between",
      gap: "var(--space-3)"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      color: "var(--text-muted)"
    }
  }, "Method"), /*#__PURE__*/React.createElement("span", null, "Single sign-on")), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      justifyContent: "space-between",
      gap: "var(--space-3)"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      color: "var(--text-muted)"
    }
  }, "Expires"), /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: "var(--font-mono)"
    }
  }, "17:40 today")), /*#__PURE__*/React.createElement(Button, {
    variant: "secondary",
    iconLeft: "log-out",
    fullWidth: true
  }, "Sign out")))) : null, tab === "notifications" ? /*#__PURE__*/React.createElement(Card, {
    title: "Notifications",
    subtitle: "Changes take effect immediately",
    footer: /*#__PURE__*/React.createElement(Button, {
      variant: "primary"
    }, "Done")
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-4)",
      maxWidth: 560
    }
  }, /*#__PURE__*/React.createElement(Switch, {
    defaultChecked: true,
    label: "Task assigned to me",
    description: "Email as it happens."
  }), /*#__PURE__*/React.createElement(Switch, {
    defaultChecked: true,
    label: "Report due in 7 days",
    description: "One digest per report, sent at 07:00."
  }), /*#__PURE__*/React.createElement(Switch, {
    defaultChecked: true,
    label: "Report marked overdue",
    description: "Email plus in-portal banner."
  }), /*#__PURE__*/React.createElement(Switch, {
    label: "Configuration drift detected",
    description: "Can be noisy on sites under active change."
  }), /*#__PURE__*/React.createElement(Switch, {
    label: "Weekly compliance digest",
    description: "Sent Mondays at 07:00 local."
  }))) : null, tab === "roles" ? /*#__PURE__*/React.createElement(Card, {
    title: "Roles & access",
    subtitle: "Managed in the corporate directory; shown here read-only",
    padding: "none",
    actions: /*#__PURE__*/React.createElement(Button, {
      size: "sm",
      variant: "ghost",
      iconLeft: "external-link"
    }, "Open directory")
  }, /*#__PURE__*/React.createElement(DataTable, {
    columns: [{
      key: "role",
      header: "Role",
      width: 180
    }, {
      key: "members",
      header: "Members",
      align: "right",
      mono: true,
      width: 100,
      sortAccessor: r => Number(r.members)
    }, {
      key: "scope",
      header: "Scope",
      wrap: true
    }, {
      key: "can",
      header: "Permissions",
      wrap: true,
      muted: true
    }, {
      key: "status",
      header: "Status",
      width: 130,
      render: r => /*#__PURE__*/React.createElement(StatusPill, {
        status: r.status
      })
    }],
    rows: [{
      id: 1,
      role: "Network admin",
      members: 8,
      scope: "All sites",
      can: "Create, edit and decommission assets; submit reports",
      status: "active"
    }, {
      id: 2,
      role: "Compliance reviewer",
      members: 5,
      scope: "All frameworks",
      can: "Accept or return submissions; export evidence packs",
      status: "active"
    }, {
      id: 3,
      role: "Site technician",
      members: 41,
      scope: "Assigned site only",
      can: "Edit location fields; complete assigned tasks",
      status: "active"
    }, {
      id: 4,
      role: "Auditor (external)",
      members: 3,
      scope: "Approved reports only",
      can: "Read approved reports and evidence",
      status: "active"
    }, {
      id: 5,
      role: "Read only",
      members: 112,
      scope: "All sites",
      can: "View inventory and reports",
      status: "active"
    }]
  })) : null, tab === "integrations" ? /*#__PURE__*/React.createElement(Card, {
    title: "Integrations",
    padding: "none"
  }, /*#__PURE__*/React.createElement("ul", {
    style: {
      listStyle: "none",
      margin: 0,
      padding: 0
    }
  }, [["NetBox", "boxes", "Inventory sync every 15 minutes", "active"], ["LibreNMS", "radio-tower", "Availability and interface counters", "active"], ["Change tickets", "ticket", "Ticket validation on inventory edits", "active"], ["Credential vault", "key-round", "Read-only token for config pulls", "exception"]].map(i => /*#__PURE__*/React.createElement("li", {
    key: i[0],
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--space-3)",
      padding: "var(--space-3) var(--pad-card)",
      borderBottom: "var(--border-width) solid var(--border-subtle)"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      width: 30,
      height: 30,
      flex: "0 0 auto",
      background: "var(--navy-50)",
      color: "var(--navy-600)",
      border: "1px solid var(--navy-100)",
      borderRadius: "var(--radius-md)"
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: i[1],
    size: 15
  })), /*#__PURE__*/React.createElement("span", {
    style: {
      flex: 1,
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      display: "block",
      fontSize: "var(--text-base)",
      fontWeight: 500
    }
  }, i[0]), /*#__PURE__*/React.createElement("span", {
    style: {
      display: "block",
      fontSize: "var(--text-xs)",
      color: "var(--text-muted)"
    }
  }, i[2])), /*#__PURE__*/React.createElement(StatusPill, {
    status: i[3]
  }), /*#__PURE__*/React.createElement(IconButton, {
    icon: "settings",
    label: "Configure " + i[0],
    size: "sm"
  }))))) : null);
}
Object.assign(window, {
  LoginScreen,
  SettingsScreen
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/nova-portal/AuthAndSettingsScreens.jsx", error: String((e && e.message) || e) }); }

// ui_kits/nova-portal/ComplianceFormScreen.jsx
try { (() => {
const {
  Card,
  Button,
  FormField,
  Input,
  Select,
  Textarea,
  Checkbox,
  Radio,
  FileUpload,
  Alert,
  Toast
} = window.PortalDesignSystem_986bf0;

/* Single-page compliance submission form: the portal's canonical form layout.
   Labels above fields, 560px field column, validation summary Alert on submit. */
function ComplianceFormScreen({
  onNavigate
}) {
  const [scope, setScope] = React.useState("region");
  const [submitted, setSubmitted] = React.useState(false);
  const [errors, setErrors] = React.useState(false);
  const [period, setPeriod] = React.useState("");
  const [files, setFiles] = React.useState([{
    name: "q3-firewall-rules-chi01.csv",
    size: "842 KB"
  }]);
  const submit = () => {
    if (!period) {
      setErrors(true);
      return;
    }
    setErrors(false);
    setSubmitted(true);
  };
  return /*#__PURE__*/React.createElement(React.Fragment, null, errors ? /*#__PURE__*/React.createElement(Alert, {
    tone: "danger",
    title: "1 field needs attention"
  }, "Reporting period is required before this submission can be queued for review.") : null, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "minmax(0,1fr) 280px",
      gap: "var(--space-6)",
      alignItems: "start"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-4)"
    }
  }, /*#__PURE__*/React.createElement(Card, {
    title: "Report details",
    footer: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Button, {
      onClick: () => onNavigate("reports")
    }, "Cancel"), /*#__PURE__*/React.createElement(Button, {
      variant: "secondary"
    }, "Save draft"), /*#__PURE__*/React.createElement(Button, {
      variant: "primary",
      onClick: submit
    }, "Submit for review"))
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--gap-field)"
    }
  }, /*#__PURE__*/React.createElement(FormField, {
    label: "Report type",
    htmlFor: "rt",
    required: true,
    hint: "Determines which controls and evidence are required."
  }, /*#__PURE__*/React.createElement(Select, {
    id: "rt",
    defaultValue: "fw",
    options: [{
      value: "fw",
      label: "Quarterly firewall rule review"
    }, {
      value: "acc",
      label: "Privileged access review"
    }, {
      value: "pci",
      label: "PCI segmentation attestation"
    }, {
      value: "cfg",
      label: "Device configuration standard conformance"
    }, {
      value: "bkp",
      label: "Backup and restore verification"
    }]
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "1fr 1fr",
      gap: "var(--space-4)",
      maxWidth: "var(--field-max)"
    }
  }, /*#__PURE__*/React.createElement(FormField, {
    label: "Reporting period",
    htmlFor: "pd",
    required: true,
    error: errors && !period ? "Select a reporting period." : undefined,
    maxWidth: "100%"
  }, /*#__PURE__*/React.createElement(Select, {
    id: "pd",
    invalid: errors && !period,
    placeholder: "Select a period",
    value: period,
    onChange: e => setPeriod(e.target.value),
    options: ["Q3 2026", "Q2 2026", "H2 2026", "Sep 2026"]
  })), /*#__PURE__*/React.createElement(FormField, {
    label: "Due date",
    htmlFor: "dd",
    maxWidth: "100%",
    hint: "Set by the framework calendar."
  }, /*#__PURE__*/React.createElement(Input, {
    id: "dd",
    mono: true,
    defaultValue: "2026-09-30",
    disabled: true
  }))), /*#__PURE__*/React.createElement(FormField, {
    label: "Scope",
    required: true,
    hint: "Wider scope pulls in more assets and more required evidence."
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-2)",
      paddingTop: 2
    }
  }, /*#__PURE__*/React.createElement(Radio, {
    name: "scope",
    value: "site",
    checked: scope === "site",
    onChange: () => setScope("site"),
    label: "Single site",
    description: "One facility and the assets terminating there."
  }), /*#__PURE__*/React.createElement(Radio, {
    name: "scope",
    value: "region",
    checked: scope === "region",
    onChange: () => setScope("region"),
    label: "Whole region",
    description: "All sites in the selected region \u2014 23 firewalls."
  }), /*#__PURE__*/React.createElement(Radio, {
    name: "scope",
    value: "global",
    checked: scope === "global",
    onChange: () => setScope("global"),
    label: "All managed assets",
    description: "4,182 devices. Expect a long evidence collection window."
  }))), scope === "region" ? /*#__PURE__*/React.createElement(FormField, {
    label: "Region",
    htmlFor: "rg",
    required: true,
    maxWidth: 280
  }, /*#__PURE__*/React.createElement(Select, {
    id: "rg",
    defaultValue: "South",
    options: ["Midwest", "Northeast", "South", "West"]
  })) : null, /*#__PURE__*/React.createElement(FormField, {
    label: "Reviewer",
    htmlFor: "rv",
    required: true,
    hint: "Must be someone other than the asset owner."
  }, /*#__PURE__*/React.createElement(Select, {
    id: "rv",
    defaultValue: "A. Bello",
    options: ["A. Bello", "M. Ruiz", "D. Okafor", "J. Lindqvist", "S. Haddad"]
  })), /*#__PURE__*/React.createElement(FormField, {
    label: "Supporting evidence",
    hint: "Rule exports, screenshots of approvals, sign-off emails. 25 MB per file."
  }, /*#__PURE__*/React.createElement(FileUpload, {
    files: files,
    onRemove: f => setFiles(files.filter(x => x.name !== f.name))
  })), /*#__PURE__*/React.createElement(FormField, {
    label: "Summary of findings",
    htmlFor: "sf",
    optional: true,
    hint: "Visible to the auditor alongside the evidence pack."
  }, /*#__PURE__*/React.createElement(Textarea, {
    id: "sf",
    rows: 4,
    placeholder: "Note any exceptions, compensating controls and remediation dates."
  })), /*#__PURE__*/React.createElement(FormField, {
    label: "Attestation",
    required: true
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-2)",
      paddingTop: 2
    }
  }, /*#__PURE__*/React.createElement(Checkbox, {
    label: "The evidence attached is complete for the stated scope and period."
  }), /*#__PURE__*/React.createElement(Checkbox, {
    label: "Exceptions listed above have an approved compensating control."
  }), /*#__PURE__*/React.createElement(Checkbox, {
    label: "Notify the reviewer by email on submission.",
    defaultChecked: true
  })))))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-4)",
      position: "sticky",
      top: 0
    }
  }, /*#__PURE__*/React.createElement(Card, {
    title: "Required evidence"
  }, /*#__PURE__*/React.createElement("ul", {
    style: {
      margin: 0,
      paddingLeft: 18,
      fontSize: "var(--text-sm)",
      color: "var(--text-body)",
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-2)"
    }
  }, /*#__PURE__*/React.createElement("li", null, "Firewall rule export per in-scope device"), /*#__PURE__*/React.createElement("li", null, "Reviewer sign-off for each ruleset"), /*#__PURE__*/React.createElement("li", null, "Justification for any rule older than 12 months"), /*#__PURE__*/React.createElement("li", null, "Change ticket references for rule additions"))), /*#__PURE__*/React.createElement(Card, {
    title: "What happens next"
  }, /*#__PURE__*/React.createElement("ol", {
    style: {
      margin: 0,
      paddingLeft: 18,
      fontSize: "var(--text-sm)",
      color: "var(--text-body)",
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-2)"
    }
  }, /*#__PURE__*/React.createElement("li", null, "Reviewer has five working days to accept or return the submission."), /*#__PURE__*/React.createElement("li", null, "Returned submissions reopen as a task assigned to you."), /*#__PURE__*/React.createElement("li", null, "Accepted submissions are locked and added to the audit pack."))))), submitted ? /*#__PURE__*/React.createElement("div", {
    style: {
      position: "fixed",
      right: "var(--space-6)",
      bottom: "var(--space-6)",
      zIndex: 1100
    }
  }, /*#__PURE__*/React.createElement(Toast, {
    tone: "success",
    title: "Submitted as REP-2294",
    onDismiss: () => setSubmitted(false),
    action: /*#__PURE__*/React.createElement(Button, {
      variant: "link",
      size: "sm",
      onClick: () => onNavigate("reports")
    }, "View register")
  }, "A. Bello has been notified and has until 22 September to review.")) : null);
}
Object.assign(window, {
  ComplianceFormScreen
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/nova-portal/ComplianceFormScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/nova-portal/ComplianceReportScreen.jsx
try { (() => {
const {
  Card,
  Button,
  IconButton,
  DataTable,
  StatusPill,
  ProgressMeter,
  Select,
  Input,
  Alert,
  MetricTile,
  Badge
} = window.PortalDesignSystem_986bf0;

/* Compliance report register + a single report's detail view. */
function ComplianceReportScreen({
  onNavigate,
  onOpenReport,
  report
}) {
  const D = window.NovaData;
  if (report) {
    return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Alert, {
      tone: "danger",
      title: "Evidence is incomplete",
      action: /*#__PURE__*/React.createElement(Button, {
        variant: "link",
        size: "sm",
        onClick: () => onNavigate("submit")
      }, "Submit evidence")
    }, "6 of 23 firewalls have no rule export attached for this period."), /*#__PURE__*/React.createElement("div", {
      style: {
        display: "grid",
        gridTemplateColumns: "repeat(4, minmax(0,1fr))",
        gap: "var(--space-4)"
      }
    }, /*#__PURE__*/React.createElement(MetricTile, {
      label: "Framework",
      value: report.framework,
      icon: "shield-check"
    }), /*#__PURE__*/React.createElement(MetricTile, {
      label: "Period",
      value: report.period,
      icon: "calendar"
    }), /*#__PURE__*/React.createElement(MetricTile, {
      label: "Due",
      value: report.due,
      icon: "clock",
      footnote: "Past due"
    }), /*#__PURE__*/React.createElement(MetricTile, {
      label: "Evidence",
      value: report.coverage + "%",
      icon: "paperclip",
      footnote: "17 of 23 items"
    })), /*#__PURE__*/React.createElement(Card, {
      title: "In-scope assets",
      subtitle: "23 firewalls across 4 regions",
      padding: "none",
      actions: /*#__PURE__*/React.createElement(Button, {
        size: "sm",
        iconLeft: "download"
      }, "Export evidence pack")
    }, /*#__PURE__*/React.createElement(DataTable, {
      columns: [{
        key: "id",
        header: "Hostname",
        mono: true,
        width: 150
      }, {
        key: "model",
        header: "Model"
      }, {
        key: "site",
        header: "Site",
        mono: true,
        width: 90
      }, {
        key: "reviewer",
        header: "Reviewer",
        width: 130
      }, {
        key: "evidence",
        header: "Evidence",
        width: 170,
        render: r => /*#__PURE__*/React.createElement("span", {
          style: {
            fontSize: "var(--text-sm)",
            color: r.evidence === "Missing" ? "var(--text-danger)" : "var(--text-body)"
          }
        }, r.evidence)
      }, {
        key: "status",
        header: "Result",
        width: 150,
        render: r => /*#__PURE__*/React.createElement(StatusPill, {
          status: r.status
        })
      }],
      rows: [{
        id: "dal03-fw-01",
        model: "Palo Alto PA-3440",
        site: "DAL-03",
        reviewer: "M. Ruiz",
        evidence: "Missing",
        status: "failed"
      }, {
        id: "chi01-fw-01",
        model: "Palo Alto PA-5410",
        site: "CHI-01",
        reviewer: "M. Ruiz",
        evidence: "rules-chi01.csv",
        status: "passed"
      }, {
        id: "atl02-fw-02",
        model: "Fortinet FG-600F",
        site: "ATL-02",
        reviewer: "A. Bello",
        evidence: "rules-atl02.csv",
        status: "passed"
      }, {
        id: "nyc04-fw-01",
        model: "Palo Alto PA-3440",
        site: "NYC-04",
        reviewer: "A. Bello",
        evidence: "rules-nyc04.csv",
        status: "exception"
      }, {
        id: "sea02-fw-01",
        model: "Fortinet FG-400F",
        site: "SEA-02",
        reviewer: "D. Okafor",
        evidence: "Missing",
        status: "failed"
      }]
    })), /*#__PURE__*/React.createElement(Card, {
      title: "Submission trail",
      padding: "none"
    }, /*#__PURE__*/React.createElement(DataTable, {
      compact: true,
      columns: [{
        key: "when",
        header: "When",
        mono: true,
        width: 150
      }, {
        key: "who",
        header: "Who",
        width: 140
      }, {
        key: "what",
        header: "Event",
        wrap: true
      }],
      rows: [{
        id: 1,
        when: "2026-09-14 11:08",
        who: "System",
        what: "Marked overdue — due date passed with incomplete evidence"
      }, {
        id: 2,
        when: "2026-09-09 15:47",
        who: "M. Ruiz",
        what: "Attached rules-chi01.csv and rules-atl02.csv"
      }, {
        id: 3,
        when: "2026-09-01 08:00",
        who: "System",
        what: "Report opened for Q3 2026 and assigned to M. Ruiz"
      }]
    })));
  }
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Card, null, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "flex-end",
      gap: "var(--gap-inline)",
      flexWrap: "wrap"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: 240
    }
  }, /*#__PURE__*/React.createElement(Input, {
    size: "sm",
    iconLeft: "search",
    placeholder: "Report ID or name"
  })), /*#__PURE__*/React.createElement(Select, {
    size: "sm",
    placeholder: "All frameworks",
    options: ["SOX", "PCI DSS 4.0", "Internal CS-11", "Internal CS-04"],
    style: {
      width: 170
    }
  }), /*#__PURE__*/React.createElement(Select, {
    size: "sm",
    placeholder: "All periods",
    options: ["Q3 2026", "H2 2026", "Sep 2026", "Aug 2026"],
    style: {
      width: 150
    }
  }), /*#__PURE__*/React.createElement(Select, {
    size: "sm",
    placeholder: "Any status",
    options: ["Overdue", "In review", "Due soon", "Submitted", "Approved"],
    style: {
      width: 150
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1
    }
  }), /*#__PURE__*/React.createElement(Button, {
    size: "sm",
    variant: "primary",
    iconLeft: "file-plus-2",
    onClick: () => onNavigate("submit")
  }, "New submission"))), /*#__PURE__*/React.createElement(Card, {
    title: "Report register",
    subtitle: "Rolling twelve months",
    padding: "none",
    actions: /*#__PURE__*/React.createElement(Badge, {
      tone: "danger"
    }, "3 overdue")
  }, /*#__PURE__*/React.createElement(DataTable, {
    onRowClick: r => onOpenReport(r),
    columns: [{
      key: "id",
      header: "Report",
      mono: true,
      width: 100
    }, {
      key: "name",
      header: "Name",
      wrap: true
    }, {
      key: "framework",
      header: "Framework",
      width: 130,
      muted: true
    }, {
      key: "period",
      header: "Period",
      width: 100,
      muted: true
    }, {
      key: "owner",
      header: "Owner",
      width: 120
    }, {
      key: "due",
      header: "Due",
      mono: true,
      width: 110
    }, {
      key: "coverage",
      header: "Evidence",
      width: 150,
      render: r => /*#__PURE__*/React.createElement(ProgressMeter, {
        size: "sm",
        value: r.coverage,
        valueText: r.coverage + "%",
        tone: r.coverage === 100 ? "success" : r.coverage < 80 ? "danger" : "warning"
      })
    }, {
      key: "status",
      header: "Status",
      width: 150,
      render: r => /*#__PURE__*/React.createElement(StatusPill, {
        status: r.status
      })
    }],
    rows: D.reports
  })), /*#__PURE__*/React.createElement(Card, {
    title: "Configuration standards",
    subtitle: "Evaluated nightly against every managed device",
    padding: "none"
  }, /*#__PURE__*/React.createElement(DataTable, {
    compact: true,
    columns: [{
      key: "id",
      header: "Standard",
      mono: true,
      width: 120
    }, {
      key: "name",
      header: "Name",
      wrap: true
    }, {
      key: "scope",
      header: "Scope",
      width: 180,
      muted: true
    }, {
      key: "pass",
      header: "Conformance",
      width: 170,
      render: r => /*#__PURE__*/React.createElement(ProgressMeter, {
        size: "sm",
        value: r.pass,
        valueText: r.pass + "%",
        tone: r.pass >= 98 ? "success" : r.pass >= 90 ? "warning" : "danger"
      })
    }],
    rows: [{
      id: "CS-11",
      name: "Device configuration baseline",
      scope: "4,182 devices",
      pass: 96
    }, {
      id: "CS-04",
      name: "Backup and restore verification",
      scope: "4,182 devices",
      pass: 99
    }, {
      id: "CS-07",
      name: "Credential rotation",
      scope: "312 accounts",
      pass: 88
    }, {
      id: "CS-19",
      name: "Perimeter rule hygiene",
      scope: "23 firewalls",
      pass: 74
    }]
  })));
}
Object.assign(window, {
  ComplianceReportScreen
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/nova-portal/ComplianceReportScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/nova-portal/DashboardScreen.jsx
try { (() => {
const {
  Card,
  Button,
  MetricTile,
  DataTable,
  StatusPill,
  ProgressMeter,
  Alert,
  Icon,
  Badge
} = window.PortalDesignSystem_986bf0;
function DashboardScreen({
  onNavigate
}) {
  const D = window.NovaData;
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Alert, {
    tone: "danger",
    title: "REP-2291 is past due",
    action: /*#__PURE__*/React.createElement(Button, {
      variant: "link",
      size: "sm",
      onClick: () => onNavigate("reports")
    }, "Open the report")
  }, "The quarterly firewall rule review was due 30 September. Evidence is 74% collected."), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "repeat(4, minmax(0,1fr))",
      gap: "var(--space-4)"
    }
  }, /*#__PURE__*/React.createElement(MetricTile, {
    label: "Assets under management",
    value: "4,182",
    icon: "server",
    delta: "+38 this month",
    deltaTone: "up",
    onClick: () => onNavigate("devices")
  }), /*#__PURE__*/React.createElement(MetricTile, {
    label: "Overdue reports",
    value: "3",
    icon: "shield-alert",
    delta: "+1 vs last week",
    deltaTone: "down",
    onClick: () => onNavigate("reports")
  }), /*#__PURE__*/React.createElement(MetricTile, {
    label: "Open tasks",
    value: "12",
    icon: "clipboard-list",
    footnote: "4 due this week",
    onClick: () => onNavigate("tasks")
  }), /*#__PURE__*/React.createElement(MetricTile, {
    label: "Config drift",
    value: "2.4",
    unit: "%",
    icon: "git-compare",
    delta: "-0.6 pts",
    deltaTone: "up"
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "minmax(0,2fr) minmax(0,1fr)",
      gap: "var(--space-4)",
      alignItems: "start"
    }
  }, /*#__PURE__*/React.createElement(Card, {
    title: "Compliance reporting",
    subtitle: "Current period",
    padding: "none",
    actions: /*#__PURE__*/React.createElement(Button, {
      size: "sm",
      onClick: () => onNavigate("reports")
    }, "View all")
  }, /*#__PURE__*/React.createElement(DataTable, {
    columns: [{
      key: "id",
      header: "Report",
      mono: true,
      width: 100
    }, {
      key: "name",
      header: "Name",
      wrap: true
    }, {
      key: "framework",
      header: "Framework",
      width: 120,
      muted: true
    }, {
      key: "due",
      header: "Due",
      mono: true,
      width: 110
    }, {
      key: "coverage",
      header: "Evidence",
      width: 140,
      render: r => /*#__PURE__*/React.createElement(ProgressMeter, {
        size: "sm",
        value: r.coverage,
        valueText: r.coverage + "%",
        tone: r.coverage === 100 ? "success" : r.coverage < 80 ? "danger" : "warning"
      })
    }, {
      key: "status",
      header: "Status",
      width: 140,
      render: r => /*#__PURE__*/React.createElement(StatusPill, {
        status: r.status
      })
    }],
    rows: D.reports.slice(0, 5),
    onRowClick: () => onNavigate("reports")
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-4)"
    }
  }, /*#__PURE__*/React.createElement(Card, {
    title: "My tasks",
    subtitle: "Assigned to you",
    padding: "none",
    actions: /*#__PURE__*/React.createElement(Button, {
      size: "sm",
      variant: "ghost",
      onClick: () => onNavigate("tasks")
    }, "All tasks")
  }, /*#__PURE__*/React.createElement("ul", {
    style: {
      listStyle: "none",
      margin: 0,
      padding: 0
    }
  }, D.tasks.slice(0, 4).map(t => /*#__PURE__*/React.createElement("li", {
    key: t.id,
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 4,
      padding: "var(--space-3) var(--pad-card)",
      borderBottom: "var(--border-width) solid var(--border-subtle)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--space-2)"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: "var(--font-mono)",
      fontSize: "var(--text-xs)",
      color: "var(--text-muted)"
    }
  }, t.id), /*#__PURE__*/React.createElement(StatusPill, {
    status: t.status
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: "var(--text-sm)",
      textWrap: "pretty"
    }
  }, t.title), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: "var(--text-xs)",
      color: "var(--text-muted)"
    }
  }, "Due ", t.due, " \xB7 ", t.assignee))))), /*#__PURE__*/React.createElement(Card, {
    title: "Recent activity"
  }, /*#__PURE__*/React.createElement("ul", {
    style: {
      listStyle: "none",
      margin: 0,
      padding: 0,
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-3)"
    }
  }, D.activity.map((a, i) => /*#__PURE__*/React.createElement("li", {
    key: i,
    style: {
      display: "flex",
      gap: "var(--space-3)",
      alignItems: "flex-start"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      color: "var(--text-subtle)",
      marginTop: 2
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: a.icon,
    size: 14
  })), /*#__PURE__*/React.createElement("span", {
    style: {
      flex: 1,
      minWidth: 0,
      fontSize: "var(--text-sm)",
      textWrap: "pretty"
    }
  }, /*#__PURE__*/React.createElement("strong", {
    style: {
      fontWeight: "var(--weight-medium)"
    }
  }, a.who), " ", a.what, /*#__PURE__*/React.createElement("span", {
    style: {
      display: "block",
      fontSize: "var(--text-xs)",
      color: "var(--text-muted)",
      marginTop: 1
    }
  }, a.when)))))))), /*#__PURE__*/React.createElement(Card, {
    title: "Inventory by region",
    subtitle: "Circuits with an active contract",
    actions: /*#__PURE__*/React.createElement(Badge, {
      tone: "neutral"
    }, "Updated 15 min ago")
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "repeat(4, minmax(0,1fr))",
      gap: "var(--space-6)"
    }
  }, [["Midwest", 1284, 1400], ["Northeast", 962, 1400], ["South", 1118, 1400], ["West", 818, 1400]].map(([r, v, m]) => /*#__PURE__*/React.createElement("div", {
    key: r,
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-2)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "baseline",
      gap: "var(--space-2)"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: "var(--text-xl)",
      fontWeight: 600,
      fontVariantNumeric: "tabular-nums",
      color: "var(--text-heading)"
    }
  }, v.toLocaleString()), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: "var(--text-xs)",
      color: "var(--text-muted)"
    }
  }, r)), /*#__PURE__*/React.createElement(ProgressMeter, {
    value: v,
    max: m,
    size: "sm"
  }))))));
}
Object.assign(window, {
  DashboardScreen
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/nova-portal/DashboardScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/nova-portal/InventoryDetailScreen.jsx
try { (() => {
const {
  Card,
  Button,
  IconButton,
  DataTable,
  StatusPill,
  Badge,
  Icon,
  Alert,
  Dialog,
  FormField,
  Select,
  Textarea,
  Tag,
  ProgressMeter
} = window.PortalDesignSystem_986bf0;
function Field({
  label,
  value,
  mono
}) {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 2,
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: "var(--text-2xs)",
      letterSpacing: "var(--tracking-caps)",
      textTransform: "uppercase",
      fontWeight: 600,
      color: "var(--text-muted)"
    }
  }, label), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: "var(--text-base)",
      fontFamily: mono ? "var(--font-mono)" : "inherit",
      fontVariantNumeric: mono ? "tabular-nums" : undefined,
      overflow: "hidden",
      textOverflow: "ellipsis"
    }
  }, value));
}
function InventoryDetailScreen({
  record,
  tab
}) {
  const r = record || window.NovaData.circuits[0];
  const [dialog, setDialog] = React.useState(false);
  if (tab === "compliance") {
    return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Alert, {
      tone: "warning",
      title: "One control is in exception until 31 Oct 2026"
    }, "Exception CE-118 approved by the network risk board on 4 Aug 2026."), /*#__PURE__*/React.createElement(Card, {
      title: "Control conformance",
      subtitle: "Internal standard CS-11 \xB7 evaluated nightly",
      padding: "none"
    }, /*#__PURE__*/React.createElement(DataTable, {
      columns: [{
        key: "id",
        header: "Control",
        mono: true,
        width: 110
      }, {
        key: "name",
        header: "Requirement",
        wrap: true
      }, {
        key: "checked",
        header: "Last checked",
        mono: true,
        width: 140,
        muted: true
      }, {
        key: "status",
        header: "Result",
        width: 150,
        render: x => /*#__PURE__*/React.createElement(StatusPill, {
          status: x.status
        })
      }],
      rows: [{
        id: "CS-11.1",
        name: "Management plane reachable only from the jump network",
        checked: "2026-09-15 02:14",
        status: "passed"
      }, {
        id: "CS-11.2",
        name: "AAA configured against both RADIUS clusters",
        checked: "2026-09-15 02:14",
        status: "passed"
      }, {
        id: "CS-11.4",
        name: "NTP peers match the approved source list",
        checked: "2026-09-15 02:14",
        status: "exception"
      }, {
        id: "CS-11.7",
        name: "Interface descriptions carry the circuit ID",
        checked: "2026-09-15 02:14",
        status: "passed"
      }, {
        id: "CS-11.9",
        name: "Config archive within 24 hours of last change",
        checked: "2026-09-15 02:14",
        status: "passed"
      }]
    })));
  }
  if (tab === "history") {
    return /*#__PURE__*/React.createElement(Card, {
      title: "Change history",
      padding: "none"
    }, /*#__PURE__*/React.createElement(DataTable, {
      compact: true,
      columns: [{
        key: "when",
        header: "When",
        mono: true,
        width: 150
      }, {
        key: "who",
        header: "Who",
        width: 140
      }, {
        key: "what",
        header: "Change",
        wrap: true
      }, {
        key: "ticket",
        header: "Ticket",
        mono: true,
        width: 120,
        muted: true
      }],
      rows: [{
        id: 1,
        when: "2026-09-12 09:41",
        who: "D. Okafor",
        what: "Bandwidth increased 500 → 1,000 Mbps",
        ticket: "CHG-14882"
      }, {
        id: 2,
        when: "2026-08-30 16:02",
        who: "Discovery job",
        what: "Handoff IP updated to 10.42.8.1",
        ticket: "—"
      }, {
        id: 3,
        when: "2026-06-18 11:20",
        who: "S. Haddad",
        what: "Contract term extended to 2027-03-31",
        ticket: "CHG-14106"
      }, {
        id: 4,
        when: "2026-02-04 08:55",
        who: "M. Ruiz",
        what: "Record created from carrier order LUM-77213",
        ticket: "CHG-12990"
      }]
    }));
  }
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "minmax(0,2fr) minmax(0,1fr)",
      gap: "var(--space-4)",
      alignItems: "start"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-4)"
    }
  }, /*#__PURE__*/React.createElement(Card, {
    title: "Circuit",
    actions: /*#__PURE__*/React.createElement(Button, {
      size: "sm",
      iconLeft: "pencil",
      onClick: () => setDialog(true)
    }, "Edit")
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "repeat(3, minmax(0,1fr))",
      gap: "var(--space-5) var(--space-6)"
    }
  }, /*#__PURE__*/React.createElement(Field, {
    label: "Circuit ID",
    value: r.id,
    mono: true
  }), /*#__PURE__*/React.createElement(Field, {
    label: "Carrier",
    value: r.carrier
  }), /*#__PURE__*/React.createElement(Field, {
    label: "Carrier order",
    value: "LUM-77213",
    mono: true
  }), /*#__PURE__*/React.createElement(Field, {
    label: "Bandwidth",
    value: r.bandwidth,
    mono: true
  }), /*#__PURE__*/React.createElement(Field, {
    label: "Handoff IP",
    value: r.ip,
    mono: true
  }), /*#__PURE__*/React.createElement(Field, {
    label: "Term ends",
    value: r.term,
    mono: true
  }), /*#__PURE__*/React.createElement(Field, {
    label: "Site",
    value: r.site + " · " + r.region
  }), /*#__PURE__*/React.createElement(Field, {
    label: "Monthly cost",
    value: r.monthly,
    mono: true
  }), /*#__PURE__*/React.createElement(Field, {
    label: "Record owner",
    value: "D. Okafor"
  }))), /*#__PURE__*/React.createElement(Card, {
    title: "Terminating devices",
    padding: "none",
    actions: /*#__PURE__*/React.createElement(Button, {
      size: "sm",
      variant: "ghost",
      iconLeft: "external-link"
    }, "Open in NetBox")
  }, /*#__PURE__*/React.createElement(DataTable, {
    compact: true,
    columns: [{
      key: "id",
      header: "Hostname",
      mono: true,
      width: 150
    }, {
      key: "model",
      header: "Model"
    }, {
      key: "role",
      header: "Role",
      width: 130,
      muted: true
    }, {
      key: "ip",
      header: "Mgmt IP",
      mono: true,
      width: 120
    }, {
      key: "status",
      header: "Standard",
      width: 140,
      render: x => /*#__PURE__*/React.createElement(StatusPill, {
        status: x.status
      })
    }],
    rows: window.NovaData.devices.slice(0, 2)
  })), /*#__PURE__*/React.createElement(Card, {
    title: "Attached documents",
    padding: "none"
  }, /*#__PURE__*/React.createElement("ul", {
    style: {
      listStyle: "none",
      margin: 0,
      padding: 0
    }
  }, [["Lumen MSA — executed.pdf", "2.4 MB", "S. Haddad", "2026-02-04"], ["LOA-CFA CHI-01.pdf", "310 KB", "D. Okafor", "2026-02-11"], ["Bandwidth upgrade approval.pdf", "185 KB", "M. Ruiz", "2026-09-12"]].map(d => /*#__PURE__*/React.createElement("li", {
    key: d[0],
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--space-3)",
      padding: "var(--space-3) var(--pad-card)",
      borderBottom: "var(--border-width) solid var(--border-subtle)"
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "file-text",
    size: 15,
    color: "var(--text-subtle)"
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      flex: 1,
      minWidth: 0,
      fontSize: "var(--text-sm)"
    }
  }, d[0]), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: "var(--text-xs)",
      color: "var(--text-muted)",
      fontVariantNumeric: "tabular-nums"
    }
  }, d[1]), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: "var(--text-xs)",
      color: "var(--text-muted)"
    }
  }, d[2], " \xB7 ", d[3]), /*#__PURE__*/React.createElement(IconButton, {
    icon: "download",
    label: "Download",
    size: "sm"
  })))))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-4)"
    }
  }, /*#__PURE__*/React.createElement(Card, {
    title: "Compliance"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-3)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--space-2)"
    }
  }, /*#__PURE__*/React.createElement(StatusPill, {
    status: r.status
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: "var(--text-xs)",
      color: "var(--text-muted)"
    }
  }, "checked 02:14 today")), /*#__PURE__*/React.createElement(ProgressMeter, {
    label: "Controls passed",
    value: 4,
    max: 5,
    valueText: "4 of 5",
    tone: "warning"
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexWrap: "wrap",
      gap: "var(--space-1)"
    }
  }, /*#__PURE__*/React.createElement(Badge, {
    tone: "brand"
  }, "CS-11"), /*#__PURE__*/React.createElement(Badge, {
    tone: "neutral"
  }, "SOX in scope"), /*#__PURE__*/React.createElement(Badge, {
    tone: "warning"
  }, "Exception CE-118")))), /*#__PURE__*/React.createElement(Card, {
    title: "Open tasks",
    padding: "none"
  }, /*#__PURE__*/React.createElement("ul", {
    style: {
      listStyle: "none",
      margin: 0,
      padding: 0
    }
  }, window.NovaData.tasks.slice(0, 2).map(t => /*#__PURE__*/React.createElement("li", {
    key: t.id,
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 3,
      padding: "var(--space-3) var(--pad-card)",
      borderBottom: "var(--border-width) solid var(--border-subtle)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--space-2)"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: "var(--font-mono)",
      fontSize: "var(--text-xs)",
      color: "var(--text-muted)"
    }
  }, t.id), /*#__PURE__*/React.createElement(StatusPill, {
    status: t.status
  })), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: "var(--text-sm)",
      textWrap: "pretty"
    }
  }, t.title))))), /*#__PURE__*/React.createElement(Card, {
    title: "Tags"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexWrap: "wrap",
      gap: "var(--space-2)"
    }
  }, /*#__PURE__*/React.createElement(Tag, {
    icon: "map-pin"
  }, "CHI-01"), /*#__PURE__*/React.createElement(Tag, null, "Tier 1 site"), /*#__PURE__*/React.createElement(Tag, null, "Dual-homed"), /*#__PURE__*/React.createElement(Tag, null, "SNMP v3"), /*#__PURE__*/React.createElement(Button, {
    variant: "ghost",
    size: "sm",
    iconLeft: "plus"
  }, "Add"))))), /*#__PURE__*/React.createElement(Dialog, {
    open: dialog,
    onClose: () => setDialog(false),
    title: "Edit " + r.id,
    description: "Changes are written to the audit log and require a change ticket.",
    width: 520,
    footer: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Button, {
      onClick: () => setDialog(false)
    }, "Cancel"), /*#__PURE__*/React.createElement(Button, {
      variant: "primary",
      onClick: () => setDialog(false)
    }, "Save changes"))
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--gap-field)"
    }
  }, /*#__PURE__*/React.createElement(FormField, {
    label: "Compliance state",
    htmlFor: "cs",
    required: true
  }, /*#__PURE__*/React.createElement(Select, {
    id: "cs",
    defaultValue: "compliant",
    options: [{
      value: "compliant",
      label: "Compliant"
    }, {
      value: "exception",
      label: "Exception"
    }, {
      value: "overdue",
      label: "Overdue"
    }]
  })), /*#__PURE__*/React.createElement(FormField, {
    label: "Change ticket",
    htmlFor: "ct",
    required: true,
    hint: "An open CHG ticket is required for inventory edits."
  }, /*#__PURE__*/React.createElement(Select, {
    id: "ct",
    placeholder: "Select a ticket",
    options: ["CHG-14882", "CHG-14901", "CHG-14903"]
  })), /*#__PURE__*/React.createElement(FormField, {
    label: "Note",
    htmlFor: "nt",
    optional: true
  }, /*#__PURE__*/React.createElement(Textarea, {
    id: "nt",
    rows: 3,
    placeholder: "What changed and why."
  })))));
}
Object.assign(window, {
  InventoryDetailScreen
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/nova-portal/InventoryDetailScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/nova-portal/InventoryListScreen.jsx
try { (() => {
const {
  Card,
  Button,
  IconButton,
  DataTable,
  StatusPill,
  Select,
  Tag,
  EmptyState,
  Toast
} = window.PortalDesignSystem_986bf0;

/* Inventory list: coarse filter toolbar, active-filter chips, selectable table, pagination.
   Search, column filters and sorting are the table's own — the toolbar above it only holds
   the two filters that are product concepts rather than columns. */
function InventoryListScreen({
  onOpenRecord
}) {
  const D = window.NovaData;
  const [region, setRegion] = React.useState("");
  const [status, setStatus] = React.useState("");
  const [selected, setSelected] = React.useState([]);
  const [pageSize] = React.useState(25);
  const [toast, setToast] = React.useState(null);
  const rows = React.useMemo(() => D.circuits.filter(c => (!region || c.region === region) && (!status || c.status === status)), [region, status]);
  const chips = [region && {
    key: "region",
    label: "Region: " + region,
    clear: () => setRegion("")
  }, status && {
    key: "status",
    label: "Status: " + status,
    clear: () => setStatus("")
  }].filter(Boolean);
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Card, {
    padding: "md",
    style: {
      padding: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "flex-end",
      gap: "var(--gap-inline)",
      flexWrap: "wrap"
    }
  }, /*#__PURE__*/React.createElement(Select, {
    size: "sm",
    placeholder: "All regions",
    value: region,
    onChange: e => setRegion(e.target.value),
    options: ["Midwest", "Northeast", "South", "West"],
    style: {
      width: 150
    }
  }), /*#__PURE__*/React.createElement(Select, {
    size: "sm",
    placeholder: "Any compliance state",
    value: status,
    onChange: e => setStatus(e.target.value),
    options: [{
      value: "compliant",
      label: "Compliant"
    }, {
      value: "due soon",
      label: "Due soon"
    }, {
      value: "overdue",
      label: "Overdue"
    }, {
      value: "exception",
      label: "Exception"
    }, {
      value: "decommissioned",
      label: "Decommissioned"
    }],
    style: {
      width: 190
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1
    }
  }), /*#__PURE__*/React.createElement(Button, {
    size: "sm",
    iconLeft: "download"
  }, "Export CSV"), /*#__PURE__*/React.createElement(IconButton, {
    icon: "refresh-cw",
    label: "Refresh",
    variant: "secondary",
    size: "sm"
  })), chips.length ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--space-2)",
      flexWrap: "wrap",
      marginTop: "var(--space-3)",
      paddingTop: "var(--space-3)",
      borderTop: "var(--border-width) solid var(--border-subtle)"
    }
  }, chips.map(c => /*#__PURE__*/React.createElement(Tag, {
    key: c.key,
    onRemove: c.clear
  }, c.label)), /*#__PURE__*/React.createElement(Button, {
    variant: "link",
    size: "sm",
    onClick: () => {
      setRegion("");
      setStatus("");
    }
  }, "Clear all")) : null), selected.length ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--space-3)",
      padding: "var(--space-2) var(--space-3)",
      background: "var(--surface-selected)",
      border: "var(--border-width) solid var(--navy-200)",
      borderRadius: "var(--radius-md)"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: "var(--text-sm)",
      fontWeight: "var(--weight-medium)"
    }
  }, selected.length, " selected"), /*#__PURE__*/React.createElement(Button, {
    size: "sm",
    iconLeft: "user-plus",
    onClick: () => setToast("Assigned " + selected.length + " circuits to D. Okafor.")
  }, "Assign owner"), /*#__PURE__*/React.createElement(Button, {
    size: "sm",
    iconLeft: "shield-check",
    onClick: () => setToast("Compliance re-check queued.")
  }, "Re-check compliance"), /*#__PURE__*/React.createElement(Button, {
    size: "sm",
    variant: "ghost",
    onClick: () => setSelected([])
  }, "Clear")) : null, /*#__PURE__*/React.createElement(Card, {
    title: "Circuits & WAN links",
    subtitle: rows.length.toLocaleString() + " of 1,284 records",
    padding: "none"
  }, /*#__PURE__*/React.createElement(DataTable, {
    search: true,
    searchPlaceholder: "Circuit ID, carrier, site, IP",
    filterable: true,
    paginated: true,
    pageSize: pageSize,
    defaultSortKey: "id",
    selectable: true,
    selected: selected,
    onSelectedChange: setSelected,
    onRowClick: r => onOpenRecord(r),
    columns: [{
      key: "id",
      header: "Circuit ID",
      mono: true,
      width: 130
    }, {
      key: "carrier",
      header: "Carrier",
      width: 110,
      filter: "select"
    }, {
      key: "site",
      header: "Site",
      mono: true,
      width: 90,
      filter: "select"
    }, {
      key: "region",
      header: "Region",
      width: 110,
      muted: true,
      filter: "select"
    }, {
      key: "bandwidth",
      header: "Bandwidth",
      align: "right",
      mono: true,
      width: 120,
      sortAccessor: r => parseFloat(r.bandwidth.replace(/[^\d.]/g, ""))
    }, {
      key: "ip",
      header: "Handoff IP",
      mono: true,
      width: 120
    }, {
      key: "term",
      header: "Term ends",
      mono: true,
      width: 110,
      muted: true
    }, {
      key: "monthly",
      header: "Monthly",
      align: "right",
      mono: true,
      width: 100,
      sortAccessor: r => parseFloat(r.monthly.replace(/[^\d.]/g, ""))
    }, {
      key: "status",
      header: "Compliance",
      width: 160,
      filter: "select",
      render: r => /*#__PURE__*/React.createElement(StatusPill, {
        status: r.status
      })
    }],
    rows: rows,
    emptyState: /*#__PURE__*/React.createElement(EmptyState, {
      compact: true,
      icon: "cable",
      title: "No circuits match these filters",
      description: "Clear a filter or widen the search to see records again.",
      action: /*#__PURE__*/React.createElement(Button, {
        size: "sm",
        onClick: () => {
          setRegion("");
          setStatus("");
        }
      }, "Clear filters")
    })
  })), toast ? /*#__PURE__*/React.createElement("div", {
    style: {
      position: "fixed",
      right: "var(--space-6)",
      bottom: "var(--space-6)",
      zIndex: 1100
    }
  }, /*#__PURE__*/React.createElement(Toast, {
    tone: "success",
    title: "Done",
    onDismiss: () => setToast(null)
  }, toast)) : null);
}
Object.assign(window, {
  InventoryListScreen
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/nova-portal/InventoryListScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/nova-portal/MultiStepFormScreen.jsx
try { (() => {
const {
  Card,
  Button,
  FormField,
  Input,
  Select,
  Checkbox,
  Textarea,
  Icon,
  Alert,
  ProgressMeter,
  Toast,
  DataTable
} = window.PortalDesignSystem_986bf0;
const STEPS = [{
  id: 1,
  label: "Asset type",
  hint: "What is being added"
}, {
  id: 2,
  label: "Identity",
  hint: "Names and identifiers"
}, {
  id: 3,
  label: "Location",
  hint: "Site and rack"
}, {
  id: 4,
  label: "Addressing",
  hint: "IP and VLAN"
}, {
  id: 5,
  label: "Review",
  hint: "Confirm and create"
}];

/* Multi-step intake wizard. Steps are a left rail, not a top stepper — the portal
   has long field lists and the rail keeps the vertical rhythm of a normal form. */
function MultiStepFormScreen({
  onNavigate
}) {
  const [step, setStep] = React.useState(2);
  const [created, setCreated] = React.useState(false);
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "232px minmax(0,1fr)",
      gap: "var(--space-6)",
      alignItems: "start"
    }
  }, /*#__PURE__*/React.createElement(Card, {
    padding: "none"
  }, /*#__PURE__*/React.createElement("ol", {
    style: {
      listStyle: "none",
      margin: 0,
      padding: 0
    }
  }, STEPS.map(s => {
    const state = s.id < step ? "done" : s.id === step ? "current" : "todo";
    return /*#__PURE__*/React.createElement("li", {
      key: s.id
    }, /*#__PURE__*/React.createElement("button", {
      type: "button",
      onClick: () => setStep(s.id),
      style: {
        display: "flex",
        alignItems: "flex-start",
        gap: "var(--space-3)",
        width: "100%",
        padding: "var(--space-3) var(--pad-card)",
        textAlign: "left",
        cursor: "pointer",
        background: state === "current" ? "var(--surface-selected)" : "transparent",
        border: 0,
        borderBottom: "var(--border-width) solid var(--border-subtle)",
        borderLeft: "var(--border-accent-width) solid " + (state === "current" ? "var(--navy-700)" : "transparent")
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: 20,
        height: 20,
        flex: "0 0 auto",
        marginTop: 1,
        borderRadius: "var(--radius-pill)",
        fontSize: "var(--text-2xs)",
        fontWeight: 600,
        background: state === "done" ? "var(--success-ink)" : state === "current" ? "var(--navy-700)" : "var(--gray-100)",
        color: state === "todo" ? "var(--text-muted)" : "#fff",
        border: state === "todo" ? "1px solid var(--border-default)" : "none"
      }
    }, state === "done" ? /*#__PURE__*/React.createElement(Icon, {
      name: "check",
      size: 12,
      strokeWidth: 3
    }) : s.id), /*#__PURE__*/React.createElement("span", {
      style: {
        minWidth: 0
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        display: "block",
        fontSize: "var(--text-sm)",
        fontWeight: state === "current" ? 600 : 500,
        color: state === "todo" ? "var(--text-muted)" : "var(--text-body)"
      }
    }, s.label), /*#__PURE__*/React.createElement("span", {
      style: {
        display: "block",
        fontSize: "var(--text-xs)",
        color: "var(--text-muted)",
        marginTop: 1
      }
    }, s.hint))));
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      padding: "var(--space-3) var(--pad-card)"
    }
  }, /*#__PURE__*/React.createElement(ProgressMeter, {
    value: step,
    max: STEPS.length,
    size: "sm",
    valueText: "Step " + step + " of " + STEPS.length
  }))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-4)"
    }
  }, step === 4 ? /*#__PURE__*/React.createElement(Alert, {
    tone: "info",
    title: "10.42.24.0/22 has 412 free addresses"
  }, "Allocations are reserved for 24 hours while the record is in draft.") : null, /*#__PURE__*/React.createElement(Card, {
    title: STEPS[step - 1].label,
    subtitle: step === 5 ? "Check the record before it is written to inventory" : "All fields are required unless marked optional",
    footer: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Button, {
      onClick: () => onNavigate("devices")
    }, "Cancel"), /*#__PURE__*/React.createElement("div", {
      style: {
        flex: 1
      }
    }), /*#__PURE__*/React.createElement(Button, {
      disabled: step === 1,
      iconLeft: "chevron-left",
      onClick: () => setStep(Math.max(1, step - 1))
    }, "Back"), step < STEPS.length ? /*#__PURE__*/React.createElement(Button, {
      variant: "primary",
      iconRight: "chevron-right",
      onClick: () => setStep(step + 1)
    }, "Continue") : /*#__PURE__*/React.createElement(Button, {
      variant: "primary",
      iconLeft: "check",
      onClick: () => setCreated(true)
    }, "Create asset"))
  }, step === 1 ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--gap-field)"
    }
  }, /*#__PURE__*/React.createElement(FormField, {
    label: "Asset class",
    htmlFor: "ac",
    required: true,
    hint: "Determines the remaining fields and the applicable configuration standard."
  }, /*#__PURE__*/React.createElement(Select, {
    id: "ac",
    defaultValue: "Router or switch",
    options: ["Circuit / WAN link", "Router or switch", "Firewall", "Wireless AP", "Site / facility", "IP block or VLAN", "License"]
  })), /*#__PURE__*/React.createElement(FormField, {
    label: "Management model",
    required: true
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-2)",
      paddingTop: 2
    }
  }, /*#__PURE__*/React.createElement(Checkbox, {
    defaultChecked: true,
    label: "Fully managed by the network team",
    description: "Included in nightly config checks and compliance scope."
  }), /*#__PURE__*/React.createElement(Checkbox, {
    label: "Monitored only",
    description: "Polled for availability; excluded from configuration standards."
  })))) : null, step === 2 ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--gap-field)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "1fr 1fr",
      gap: "var(--space-4)",
      maxWidth: "var(--field-max)"
    }
  }, /*#__PURE__*/React.createElement(FormField, {
    label: "Hostname",
    htmlFor: "hn",
    required: true,
    hint: "site-role-nn, lower case.",
    maxWidth: "100%"
  }, /*#__PURE__*/React.createElement(Input, {
    id: "hn",
    mono: true,
    placeholder: "phx01-sw-02"
  })), /*#__PURE__*/React.createElement(FormField, {
    label: "Serial number",
    htmlFor: "sn",
    required: true,
    maxWidth: "100%"
  }, /*#__PURE__*/React.createElement(Input, {
    id: "sn",
    mono: true,
    placeholder: "FDO2416R0AB"
  }))), /*#__PURE__*/React.createElement(FormField, {
    label: "Model",
    htmlFor: "md",
    required: true
  }, /*#__PURE__*/React.createElement(Select, {
    id: "md",
    placeholder: "Select a model",
    options: ["Cisco C8500-12X", "Cisco C9300-48P", "Arista 7050SX3", "Juniper MX204", "Palo Alto PA-3440"]
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "1fr 1fr",
      gap: "var(--space-4)",
      maxWidth: "var(--field-max)"
    }
  }, /*#__PURE__*/React.createElement(FormField, {
    label: "Role",
    htmlFor: "rl",
    required: true,
    maxWidth: "100%"
  }, /*#__PURE__*/React.createElement(Select, {
    id: "rl",
    defaultValue: "Access switch",
    options: ["Core router", "Distribution switch", "Access switch", "Perimeter firewall", "Wireless controller"]
  })), /*#__PURE__*/React.createElement(FormField, {
    label: "Owner",
    htmlFor: "ow",
    required: true,
    maxWidth: "100%"
  }, /*#__PURE__*/React.createElement(Select, {
    id: "ow",
    defaultValue: "D. Okafor",
    options: ["D. Okafor", "M. Ruiz", "A. Bello", "J. Lindqvist"]
  }))), /*#__PURE__*/React.createElement(FormField, {
    label: "Asset tag",
    htmlFor: "tg",
    optional: true,
    hint: "Finance asset tag, if one has been issued.",
    maxWidth: 280
  }, /*#__PURE__*/React.createElement(Input, {
    id: "tg",
    mono: true,
    placeholder: "AT-0000000"
  }))) : null, step === 3 ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--gap-field)"
    }
  }, /*#__PURE__*/React.createElement(FormField, {
    label: "Site",
    htmlFor: "st",
    required: true
  }, /*#__PURE__*/React.createElement(Select, {
    id: "st",
    defaultValue: "PHX-01",
    options: ["CHI-01", "DAL-03", "ATL-02", "BOS-01", "NYC-04", "SEA-02", "PHX-01", "DEN-01"]
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "1fr 1fr 1fr",
      gap: "var(--space-4)",
      maxWidth: "var(--field-max)"
    }
  }, /*#__PURE__*/React.createElement(FormField, {
    label: "Room",
    htmlFor: "rm",
    required: true,
    maxWidth: "100%"
  }, /*#__PURE__*/React.createElement(Input, {
    id: "rm",
    defaultValue: "MDF"
  })), /*#__PURE__*/React.createElement(FormField, {
    label: "Rack",
    htmlFor: "rk",
    required: true,
    maxWidth: "100%"
  }, /*#__PURE__*/React.createElement(Input, {
    id: "rk",
    mono: true,
    defaultValue: "R14"
  })), /*#__PURE__*/React.createElement(FormField, {
    label: "Rack unit",
    htmlFor: "ru",
    required: true,
    maxWidth: "100%"
  }, /*#__PURE__*/React.createElement(Input, {
    id: "ru",
    mono: true,
    defaultValue: "22",
    suffix: "U"
  }))), /*#__PURE__*/React.createElement(FormField, {
    label: "Access notes",
    htmlFor: "an",
    optional: true,
    hint: "Escort requirements, badge zones, loading dock hours."
  }, /*#__PURE__*/React.createElement(Textarea, {
    id: "an",
    rows: 3
  }))) : null, step === 4 ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--gap-field)"
    }
  }, /*#__PURE__*/React.createElement(FormField, {
    label: "IP block",
    htmlFor: "ib",
    required: true,
    hint: "Management subnet for the site."
  }, /*#__PURE__*/React.createElement(Select, {
    id: "ib",
    defaultValue: "10.42.24.0/22",
    options: ["10.42.24.0/22", "10.60.40.0/22", "10.108.8.0/22"]
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "1fr 1fr",
      gap: "var(--space-4)",
      maxWidth: "var(--field-max)"
    }
  }, /*#__PURE__*/React.createElement(FormField, {
    label: "Management IP",
    htmlFor: "mi",
    required: true,
    maxWidth: "100%"
  }, /*#__PURE__*/React.createElement(Input, {
    id: "mi",
    mono: true,
    defaultValue: "10.42.24.18"
  })), /*#__PURE__*/React.createElement(FormField, {
    label: "Management VLAN",
    htmlFor: "mv",
    required: true,
    maxWidth: "100%"
  }, /*#__PURE__*/React.createElement(Input, {
    id: "mv",
    mono: true,
    defaultValue: "412"
  }))), /*#__PURE__*/React.createElement(FormField, {
    label: "SNMP profile",
    htmlFor: "sp",
    required: true
  }, /*#__PURE__*/React.createElement(Select, {
    id: "sp",
    defaultValue: "v3-auth-priv",
    options: ["v3-auth-priv", "v3-auth-nopriv", "v2c-readonly (deprecated)"]
  }))) : null, step === 5 ? /*#__PURE__*/React.createElement(DataTable, {
    compact: true,
    columns: [{
      key: "field",
      header: "Field",
      width: 200,
      muted: true
    }, {
      key: "value",
      header: "Value",
      mono: true
    }],
    rows: [{
      id: 1,
      field: "Asset class",
      value: "Router or switch"
    }, {
      id: 2,
      field: "Hostname",
      value: "phx01-sw-02"
    }, {
      id: 3,
      field: "Model",
      value: "Cisco C9300-48P"
    }, {
      id: 4,
      field: "Role",
      value: "Access switch"
    }, {
      id: 5,
      field: "Site / rack",
      value: "PHX-01 · MDF · R14 · 22U"
    }, {
      id: 6,
      field: "Management IP",
      value: "10.42.24.18"
    }, {
      id: 7,
      field: "Management VLAN",
      value: "412"
    }, {
      id: 8,
      field: "SNMP profile",
      value: "v3-auth-priv"
    }, {
      id: 9,
      field: "Compliance scope",
      value: "CS-11, CS-04"
    }]
  }) : null))), created ? /*#__PURE__*/React.createElement("div", {
    style: {
      position: "fixed",
      right: "var(--space-6)",
      bottom: "var(--space-6)",
      zIndex: 1100
    }
  }, /*#__PURE__*/React.createElement(Toast, {
    tone: "success",
    title: "phx01-sw-02 created",
    onDismiss: () => setCreated(false),
    action: /*#__PURE__*/React.createElement(Button, {
      variant: "link",
      size: "sm",
      onClick: () => onNavigate("devices")
    }, "Open record")
  }, "Added to inventory and queued for its first configuration check tonight.")) : null);
}
Object.assign(window, {
  MultiStepFormScreen
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/nova-portal/MultiStepFormScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/nova-portal/TaskScreens.jsx
try { (() => {
const {
  Card,
  Button,
  IconButton,
  DataTable,
  StatusPill,
  Badge,
  Select,
  Input,
  Dialog,
  FormField,
  Textarea,
  Toast,
  EmptyState,
  Icon,
  Tabs
} = window.PortalDesignSystem_986bf0;
function TaskListScreen({
  onOpenTask
}) {
  const D = window.NovaData;
  const [scope, setScope] = React.useState("mine");
  const [selected, setSelected] = React.useState([]);
  const [reassign, setReassign] = React.useState(false);
  const [toast, setToast] = React.useState(null);
  const rows = scope === "mine" ? D.tasks.filter(t => t.assignee === "D. Okafor") : scope === "overdue" ? D.tasks.filter(t => t.status === "overdue") : D.tasks;
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Card, null, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "flex-end",
      gap: "var(--gap-inline)",
      flexWrap: "wrap"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: 240
    }
  }, /*#__PURE__*/React.createElement(Input, {
    size: "sm",
    iconLeft: "search",
    placeholder: "Task ID or title"
  })), /*#__PURE__*/React.createElement(Select, {
    size: "sm",
    placeholder: "Any priority",
    options: ["High", "Medium", "Low"],
    style: {
      width: 140
    }
  }), /*#__PURE__*/React.createElement(Select, {
    size: "sm",
    placeholder: "Any assignee",
    options: ["D. Okafor", "M. Ruiz", "A. Bello", "J. Lindqvist", "S. Haddad"],
    style: {
      width: 160
    }
  }), /*#__PURE__*/React.createElement(Select, {
    size: "sm",
    placeholder: "Any due date",
    options: ["Overdue", "Due this week", "Due this month"],
    style: {
      width: 150
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1
    }
  }), /*#__PURE__*/React.createElement(Button, {
    size: "sm",
    variant: "primary",
    iconLeft: "plus"
  }, "New task"))), selected.length ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--space-3)",
      padding: "var(--space-2) var(--space-3)",
      background: "var(--surface-selected)",
      border: "var(--border-width) solid var(--navy-200)",
      borderRadius: "var(--radius-md)"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: "var(--text-sm)",
      fontWeight: "var(--weight-medium)"
    }
  }, selected.length, " selected"), /*#__PURE__*/React.createElement(Button, {
    size: "sm",
    iconLeft: "user-plus",
    onClick: () => setReassign(true)
  }, "Reassign"), /*#__PURE__*/React.createElement(Button, {
    size: "sm",
    iconLeft: "calendar"
  }, "Change due date"), /*#__PURE__*/React.createElement(Button, {
    size: "sm",
    variant: "ghost",
    onClick: () => setSelected([])
  }, "Clear")) : null, /*#__PURE__*/React.createElement(Card, {
    title: "Task assignments",
    subtitle: rows.length + " tasks",
    padding: "none",
    actions: /*#__PURE__*/React.createElement("div", {
      style: {
        display: "flex",
        gap: 4
      }
    }, [["mine", "Assigned to me"], ["team", "My team"], ["overdue", "Overdue"]].map(([k, l]) => /*#__PURE__*/React.createElement(Button, {
      key: k,
      size: "sm",
      variant: scope === k ? "secondary" : "ghost",
      onClick: () => setScope(k)
    }, l)))
  }, /*#__PURE__*/React.createElement(DataTable, {
    selectable: true,
    selected: selected,
    onSelectedChange: setSelected,
    onRowClick: t => onOpenTask(t),
    columns: [{
      key: "id",
      header: "Task",
      mono: true,
      width: 110
    }, {
      key: "title",
      header: "Title",
      wrap: true
    }, {
      key: "related",
      header: "Related record",
      mono: true,
      width: 150,
      muted: true
    }, {
      key: "assignee",
      header: "Assignee",
      width: 130
    }, {
      key: "priority",
      header: "Priority",
      width: 110,
      render: t => /*#__PURE__*/React.createElement(Badge, {
        tone: t.priority === "High" ? "danger" : t.priority === "Medium" ? "warning" : "neutral"
      }, t.priority)
    }, {
      key: "due",
      header: "Due",
      mono: true,
      width: 110
    }, {
      key: "status",
      header: "Status",
      width: 140,
      render: t => /*#__PURE__*/React.createElement(StatusPill, {
        status: t.status
      })
    }],
    rows: rows,
    emptyState: /*#__PURE__*/React.createElement(EmptyState, {
      compact: true,
      icon: "clipboard-check",
      title: "No tasks in this view",
      description: "Nothing is assigned to you right now. New assignments appear here."
    })
  })), /*#__PURE__*/React.createElement(Dialog, {
    open: reassign,
    onClose: () => setReassign(false),
    title: "Reassign " + selected.length + " task" + (selected.length === 1 ? "" : "s"),
    description: "Assignees are notified by email immediately.",
    width: 420,
    footer: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Button, {
      onClick: () => setReassign(false)
    }, "Cancel"), /*#__PURE__*/React.createElement(Button, {
      variant: "primary",
      onClick: () => {
        setReassign(false);
        setSelected([]);
        setToast("Now assigned to M. Ruiz.");
      }
    }, "Reassign"))
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--gap-field)"
    }
  }, /*#__PURE__*/React.createElement(FormField, {
    label: "Assign to",
    htmlFor: "at",
    required: true
  }, /*#__PURE__*/React.createElement(Select, {
    id: "at",
    defaultValue: "M. Ruiz",
    options: ["M. Ruiz", "A. Bello", "J. Lindqvist", "S. Haddad"]
  })), /*#__PURE__*/React.createElement(FormField, {
    label: "Note to assignee",
    htmlFor: "na",
    optional: true
  }, /*#__PURE__*/React.createElement(Textarea, {
    id: "na",
    rows: 3,
    placeholder: "Why this is moving."
  })))), toast ? /*#__PURE__*/React.createElement("div", {
    style: {
      position: "fixed",
      right: "var(--space-6)",
      bottom: "var(--space-6)",
      zIndex: 1100
    }
  }, /*#__PURE__*/React.createElement(Toast, {
    tone: "success",
    title: "Tasks reassigned",
    onDismiss: () => setToast(null),
    action: /*#__PURE__*/React.createElement(Button, {
      variant: "link",
      size: "sm",
      onClick: () => setToast(null)
    }, "Undo")
  }, toast)) : null);
}
function TaskDetailScreen({
  task,
  onNavigate
}) {
  const t = task || window.NovaData.tasks[0];
  const [done, setDone] = React.useState(false);
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "minmax(0,2fr) minmax(0,1fr)",
      gap: "var(--space-4)",
      alignItems: "start"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-4)"
    }
  }, /*#__PURE__*/React.createElement(Card, {
    title: t.title,
    subtitle: "Opened 12 September 2026 by the compliance scheduler",
    footer: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Button, {
      onClick: () => onNavigate("tasks")
    }, "Back to tasks"), /*#__PURE__*/React.createElement(Button, {
      variant: "secondary",
      iconLeft: "user-plus"
    }, "Reassign"), /*#__PURE__*/React.createElement(Button, {
      variant: "primary",
      iconLeft: "check",
      onClick: () => setDone(true)
    }, "Mark complete"))
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-4)"
    }
  }, /*#__PURE__*/React.createElement("p", {
    style: {
      margin: 0,
      fontSize: "var(--text-base)",
      textWrap: "pretty",
      maxWidth: 680
    }
  }, "Export the current firewall rule set from dal03-fw-01 and attach it to REP-2291. The export must include rule descriptions, last-hit timestamps and the change ticket recorded against each rule. Rules with no hit in 180 days need a justification or a removal ticket."), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "repeat(4, minmax(0,1fr))",
      gap: "var(--space-4)"
    }
  }, [["Task", t.id], ["Assignee", t.assignee], ["Due", t.due], ["Priority", t.priority]].map(([l, v]) => /*#__PURE__*/React.createElement("div", {
    key: l,
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 2
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: "var(--text-2xs)",
      letterSpacing: "var(--tracking-caps)",
      textTransform: "uppercase",
      fontWeight: 600,
      color: "var(--text-muted)"
    }
  }, l), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: "var(--text-base)",
      fontFamily: l === "Task" || l === "Due" ? "var(--font-mono)" : "inherit"
    }
  }, v)))))), /*#__PURE__*/React.createElement(Card, {
    title: "Checklist",
    padding: "none"
  }, /*#__PURE__*/React.createElement("ul", {
    style: {
      listStyle: "none",
      margin: 0,
      padding: 0
    }
  }, [["Pull rule export from the firewall", true], ["Reconcile rules against change tickets", true], ["Flag rules with no hits in 180 days", false], ["Attach export to REP-2291", false]].map(([label, complete]) => /*#__PURE__*/React.createElement("li", {
    key: label,
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--space-3)",
      padding: "var(--space-3) var(--pad-card)",
      borderBottom: "var(--border-width) solid var(--border-subtle)"
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: complete ? "circle-check" : "circle",
    size: 15,
    color: complete ? "var(--success-ink)" : "var(--text-subtle)"
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      flex: 1,
      fontSize: "var(--text-sm)",
      color: complete ? "var(--text-muted)" : "var(--text-body)",
      textDecoration: complete ? "line-through" : "none"
    }
  }, label))))), /*#__PURE__*/React.createElement(Card, {
    title: "Comments"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-4)"
    }
  }, [["M. Ruiz", "13 Sep 09:12", "Export is blocked — the read-only API token expired. Raised CHG-14903 to rotate it."], ["D. Okafor", "13 Sep 10:40", "Token rotated. Try again and let me know if the 403 persists."]].map(c => /*#__PURE__*/React.createElement("div", {
    key: c[1],
    style: {
      display: "flex",
      gap: "var(--space-3)"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      width: 28,
      height: 28,
      flex: "0 0 auto",
      background: "var(--navy-50)",
      color: "var(--navy-600)",
      borderRadius: "var(--radius-pill)",
      fontSize: "var(--text-2xs)",
      fontWeight: 600
    }
  }, c[0].split(" ").map(s => s[0]).join("")), /*#__PURE__*/React.createElement("div", {
    style: {
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: "var(--space-2)",
      alignItems: "baseline"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: "var(--text-sm)",
      fontWeight: "var(--weight-medium)"
    }
  }, c[0]), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: "var(--text-xs)",
      color: "var(--text-muted)"
    }
  }, c[1])), /*#__PURE__*/React.createElement("p", {
    style: {
      margin: "2px 0 0",
      fontSize: "var(--text-sm)",
      textWrap: "pretty"
    }
  }, c[2])))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-2)",
      alignItems: "flex-start"
    }
  }, /*#__PURE__*/React.createElement(Textarea, {
    rows: 2,
    placeholder: "Add a comment"
  }), /*#__PURE__*/React.createElement(Button, {
    size: "sm"
  }, "Comment"))))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-4)"
    }
  }, /*#__PURE__*/React.createElement(Card, {
    title: "Status"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-3)"
    }
  }, /*#__PURE__*/React.createElement(StatusPill, {
    status: done ? "approved" : t.status
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: "var(--text-sm)",
      color: "var(--text-muted)"
    }
  }, "Blocked 2 days \xB7 reopened once"))), /*#__PURE__*/React.createElement(Card, {
    title: "Related records",
    padding: "none"
  }, /*#__PURE__*/React.createElement("ul", {
    style: {
      listStyle: "none",
      margin: 0,
      padding: 0
    }
  }, [["shield-check", "REP-2291", "Quarterly firewall rule review"], ["server", "dal03-fw-01", "Palo Alto PA-3440 · DAL-03"], ["ticket", "CHG-14903", "Rotate read-only API token"]].map(r => /*#__PURE__*/React.createElement("li", {
    key: r[1],
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--space-3)",
      padding: "var(--space-3) var(--pad-card)",
      borderBottom: "var(--border-width) solid var(--border-subtle)"
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: r[0],
    size: 15,
    color: "var(--text-subtle)"
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      display: "block",
      fontFamily: "var(--font-mono)",
      fontSize: "var(--text-sm)",
      color: "var(--text-link)"
    }
  }, r[1]), /*#__PURE__*/React.createElement("span", {
    style: {
      display: "block",
      fontSize: "var(--text-xs)",
      color: "var(--text-muted)"
    }
  }, r[2])))))))));
}
Object.assign(window, {
  TaskListScreen,
  TaskDetailScreen
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/nova-portal/TaskScreens.jsx", error: String((e && e.message) || e) }); }

// ui_kits/nova-portal/ToolLauncherScreen.jsx
try { (() => {
const {
  Card,
  Button,
  Icon,
  Input,
  Badge,
  Alert,
  Tag
} = window.PortalDesignSystem_986bf0;

/* Tool launcher: the portal is also the front door to everything else the team uses. */
function ToolLauncherScreen() {
  const [query, setQuery] = React.useState("");
  const tools = window.NovaData.tools.filter(t => (t.name + t.description + t.group).toLowerCase().includes(query.trim().toLowerCase()));
  const groups = ["Monitoring", "Source of truth", "Change", "Access", "Planning"].filter(g => tools.some(t => t.group === g));
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Alert, {
    tone: "info",
    title: "Single sign-on is active"
  }, "Links open in a new tab using your portal session. The credential vault asks for a second factor."), /*#__PURE__*/React.createElement("div", {
    style: {
      width: 300
    }
  }, /*#__PURE__*/React.createElement(Input, {
    size: "sm",
    iconLeft: "search",
    placeholder: "Search tools",
    value: query,
    onChange: e => setQuery(e.target.value)
  })), groups.map(g => /*#__PURE__*/React.createElement("div", {
    key: g,
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "var(--space-3)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--space-2)"
    }
  }, /*#__PURE__*/React.createElement("h2", {
    style: {
      fontSize: "var(--type-section-size)",
      fontWeight: "var(--type-section-weight)"
    }
  }, g), /*#__PURE__*/React.createElement(Badge, {
    tone: "neutral"
  }, tools.filter(t => t.group === g).length)), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
      gap: "var(--space-4)"
    }
  }, tools.filter(t => t.group === g).map(t => /*#__PURE__*/React.createElement(ToolCard, {
    key: t.id,
    tool: t
  }))))), /*#__PURE__*/React.createElement(Card, {
    title: "Request a link",
    subtitle: "Tools are added by the portal owners"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--space-4)",
      flexWrap: "wrap"
    }
  }, /*#__PURE__*/React.createElement("p", {
    style: {
      margin: 0,
      flex: 1,
      minWidth: 280,
      fontSize: "var(--text-sm)",
      color: "var(--text-muted)",
      textWrap: "pretty"
    }
  }, "Missing something your team uses daily? Raise a request and include the URL, the owning team and whether it supports single sign-on."), /*#__PURE__*/React.createElement(Button, {
    iconLeft: "plus"
  }, "Request a tool"))));
}
function ToolCard({
  tool
}) {
  const [hover, setHover] = React.useState(false);
  return /*#__PURE__*/React.createElement("a", {
    href: "#",
    onClick: e => e.preventDefault(),
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      display: "flex",
      gap: "var(--space-3)",
      alignItems: "flex-start",
      padding: "var(--pad-card)",
      textDecoration: "none",
      border: "var(--border-width) solid " + (hover ? "var(--border-strong)" : "var(--border-default)"),
      borderRadius: "var(--radius-card)",
      background: "var(--surface-card)",
      transition: "var(--transition-control)",
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      width: 34,
      height: 34,
      flex: "0 0 auto",
      background: "var(--navy-50)",
      color: "var(--navy-600)",
      border: "var(--border-width) solid var(--navy-100)",
      borderRadius: "var(--radius-md)"
    }
  }, /*#__PURE__*/React.createElement(Icon, {
    name: tool.icon,
    size: 17
  })), /*#__PURE__*/React.createElement("span", {
    style: {
      minWidth: 0,
      flex: 1
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "var(--space-15)"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: "var(--text-base)",
      fontWeight: "var(--weight-semibold)",
      color: "var(--text-heading)"
    }
  }, tool.name), /*#__PURE__*/React.createElement(Icon, {
    name: "external-link",
    size: 12,
    color: "var(--text-subtle)"
  })), /*#__PURE__*/React.createElement("span", {
    style: {
      display: "block",
      marginTop: 3,
      fontSize: "var(--text-sm)",
      color: "var(--text-muted)",
      textWrap: "pretty"
    }
  }, tool.description)));
}
Object.assign(window, {
  ToolLauncherScreen
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/nova-portal/ToolLauncherScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/nova-portal/data.js
try { (() => {
window.NovaData = {
  navSections: [{
    id: "ops",
    items: [{
      id: "dashboard",
      label: "Dashboard",
      icon: "layout-dashboard"
    }, {
      id: "sites",
      label: "Sites & facilities",
      icon: "building-2"
    }]
  }, {
    id: "inventory",
    label: "Inventory",
    items: [{
      id: "circuits",
      label: "Circuits & WAN",
      icon: "cable"
    }, {
      id: "devices",
      label: "Routers & switches",
      icon: "server"
    },
    /* Third level: Inventory → Global → Cisco. Groups open themselves when something
       under them is the active route. */
    {
      id: "global",
      label: "Global",
      icon: "globe",
      items: [{
        id: "vendor-cisco",
        label: "Cisco"
      }, {
        id: "vendor-juniper",
        label: "Juniper"
      }, {
        id: "vendor-arista",
        label: "Arista"
      }]
    }, {
      id: "firewalls",
      label: "Firewalls",
      icon: "shield"
    }, {
      id: "wireless",
      label: "Wireless APs",
      icon: "wifi"
    }, {
      id: "addressing",
      label: "IP blocks & VLANs",
      icon: "network"
    }, {
      id: "carriers",
      label: "Carriers & contracts",
      icon: "file-text"
    }, {
      id: "licenses",
      label: "Licenses",
      icon: "key-round"
    }]
  }, {
    id: "compliance",
    label: "Compliance",
    items: [{
      id: "reports",
      label: "Reports",
      icon: "shield-check",
      count: 3,
      countTone: "danger"
    }, {
      id: "submit",
      label: "Submit a report",
      icon: "file-plus-2"
    }, {
      id: "standards",
      label: "Config standards",
      icon: "list-checks"
    }]
  }, {
    id: "work",
    label: "Work",
    items: [{
      id: "tasks",
      label: "Task assignments",
      icon: "clipboard-list",
      count: 12
    }, {
      id: "intake",
      label: "New asset intake",
      icon: "package-plus"
    }]
  }, {
    id: "tools",
    label: "Linked tools",
    items: [{
      id: "tool-launcher",
      label: "All tools",
      icon: "layout-grid"
    }, {
      id: "t-grafana",
      label: "Grafana",
      icon: "activity",
      external: true
    }, {
      id: "t-netbox",
      label: "NetBox",
      icon: "boxes",
      external: true
    }, {
      id: "t-jira",
      label: "Change tickets",
      icon: "ticket",
      external: true
    }]
  }, {
    id: "admin",
    label: "Administration",
    items: [{
      id: "settings",
      label: "Settings",
      icon: "settings"
    }],
    defaultOpen: false
  }],
  circuits: [{
    id: "CKT-40182",
    carrier: "Lumen",
    site: "CHI-01",
    region: "Midwest",
    bandwidth: "1,000 Mbps",
    ip: "10.42.8.1",
    status: "compliant",
    term: "2027-03-31",
    monthly: "$4,180"
  }, {
    id: "CKT-40219",
    carrier: "Zayo",
    site: "DAL-03",
    region: "South",
    bandwidth: "500 Mbps",
    ip: "10.42.16.1",
    status: "due soon",
    term: "2026-11-30",
    monthly: "$2,640"
  }, {
    id: "CKT-40244",
    carrier: "AT&T",
    site: "ATL-02",
    region: "South",
    bandwidth: "10,000 Mbps",
    ip: "10.108.2.1",
    status: "overdue",
    term: "2028-06-30",
    monthly: "$11,900"
  }, {
    id: "CKT-40301",
    carrier: "Cogent",
    site: "DEN-01",
    region: "West",
    bandwidth: "250 Mbps",
    ip: "10.60.4.1",
    status: "decommissioned",
    term: "2026-09-30",
    monthly: "$980"
  }, {
    id: "CKT-40355",
    carrier: "Lumen",
    site: "BOS-01",
    region: "Northeast",
    bandwidth: "1,000 Mbps",
    ip: "10.12.8.1",
    status: "compliant",
    term: "2027-01-31",
    monthly: "$4,320"
  }, {
    id: "CKT-40388",
    carrier: "Verizon",
    site: "NYC-04",
    region: "Northeast",
    bandwidth: "2,000 Mbps",
    ip: "10.12.32.1",
    status: "exception",
    term: "2027-08-31",
    monthly: "$6,750"
  }, {
    id: "CKT-40402",
    carrier: "Zayo",
    site: "SEA-02",
    region: "West",
    bandwidth: "1,000 Mbps",
    ip: "10.60.24.1",
    status: "compliant",
    term: "2027-05-31",
    monthly: "$4,090"
  }, {
    id: "CKT-40417",
    carrier: "Comcast",
    site: "PHX-01",
    region: "West",
    bandwidth: "500 Mbps",
    ip: "10.60.40.1",
    status: "due soon",
    term: "2026-12-31",
    monthly: "$1,860"
  }],
  devices: [{
    id: "chi01-cr-01",
    model: "Cisco C8500-12X",
    role: "Core router",
    site: "CHI-01",
    ip: "10.42.0.11",
    os: "IOS-XE 17.12.3",
    status: "compliant"
  }, {
    id: "chi01-sw-04",
    model: "Arista 7050SX3",
    role: "Access switch",
    site: "CHI-01",
    ip: "10.42.0.34",
    os: "EOS 4.31.2F",
    status: "exception"
  }, {
    id: "dal03-fw-01",
    model: "Palo Alto PA-3440",
    role: "Perimeter firewall",
    site: "DAL-03",
    ip: "10.42.16.9",
    os: "PAN-OS 11.1.2",
    status: "overdue"
  }, {
    id: "atl02-cr-02",
    model: "Juniper MX204",
    role: "Core router",
    site: "ATL-02",
    ip: "10.108.0.12",
    os: "Junos 22.4R3",
    status: "compliant"
  }, {
    id: "bos01-ap-17",
    model: "Aruba AP-635",
    role: "Wireless AP",
    site: "BOS-01",
    ip: "10.12.9.17",
    os: "ArubaOS 10.5",
    status: "compliant"
  }],
  reports: [{
    id: "REP-2291",
    name: "Quarterly firewall rule review",
    framework: "SOX",
    period: "Q3 2026",
    owner: "M. Ruiz",
    due: "2026-09-30",
    status: "overdue",
    coverage: 74
  }, {
    id: "REP-2288",
    name: "Privileged access review",
    framework: "SOX",
    period: "Q3 2026",
    owner: "A. Bello",
    due: "2026-09-30",
    status: "in review",
    coverage: 100
  }, {
    id: "REP-2284",
    name: "PCI segmentation attestation",
    framework: "PCI DSS 4.0",
    period: "H2 2026",
    owner: "D. Okafor",
    due: "2026-10-15",
    status: "due soon",
    coverage: 88
  }, {
    id: "REP-2279",
    name: "Device configuration standard conformance",
    framework: "Internal CS-11",
    period: "Sep 2026",
    owner: "J. Lindqvist",
    died: "",
    due: "2026-09-20",
    status: "submitted",
    coverage: 96
  }, {
    id: "REP-2271",
    name: "Change-control evidence sample",
    framework: "SOX",
    period: "Aug 2026",
    owner: "M. Ruiz",
    due: "2026-09-05",
    status: "approved",
    coverage: 100
  }, {
    id: "REP-2265",
    name: "Backup and restore verification",
    framework: "Internal CS-04",
    period: "Aug 2026",
    owner: "S. Haddad",
    due: "2026-09-05",
    status: "approved",
    coverage: 100
  }],
  tasks: [{
    id: "TSK-8841",
    title: "Collect firewall rule export for DAL-03",
    assignee: "M. Ruiz",
    related: "REP-2291",
    due: "2026-09-17",
    priority: "High",
    status: "overdue"
  }, {
    id: "TSK-8846",
    title: "Confirm decommission of CKT-40301",
    assignee: "D. Okafor",
    related: "CKT-40301",
    due: "2026-09-18",
    priority: "Medium",
    status: "pending"
  }, {
    id: "TSK-8851",
    title: "Upload access review sign-off (Q3)",
    assignee: "A. Bello",
    related: "REP-2288",
    due: "2026-09-19",
    priority: "High",
    status: "in review"
  }, {
    id: "TSK-8853",
    title: "Remediate NTP drift on chi01-sw-04",
    assignee: "J. Lindqvist",
    related: "chi01-sw-04",
    due: "2026-09-22",
    priority: "Medium",
    status: "pending"
  }, {
    id: "TSK-8858",
    title: "Renew Zayo contract for SEA-02",
    assignee: "S. Haddad",
    related: "CKT-40402",
    due: "2026-09-30",
    priority: "Low",
    status: "planned"
  }, {
    id: "TSK-8861",
    title: "Validate VLAN 412 documentation",
    assignee: "D. Okafor",
    related: "10.42.24.0/22",
    due: "2026-10-02",
    priority: "Low",
    status: "planned"
  }],
  tools: [{
    id: "grafana",
    name: "Grafana",
    icon: "activity",
    group: "Monitoring",
    description: "Link utilisation, latency and interface error dashboards."
  }, {
    id: "netbox",
    name: "NetBox",
    icon: "boxes",
    group: "Source of truth",
    description: "Upstream DCIM and IPAM records that feed this portal."
  }, {
    id: "librenms",
    name: "LibreNMS",
    icon: "radio-tower",
    group: "Monitoring",
    description: "SNMP polling, alerting and device availability."
  }, {
    id: "jira",
    name: "Change tickets",
    icon: "ticket",
    group: "Change",
    description: "Raise and track change requests and approvals."
  }, {
    id: "confluence",
    name: "Runbooks",
    icon: "book-open",
    group: "Change",
    description: "Site runbooks, escalation paths and maintenance windows."
  }, {
    id: "vault",
    name: "Credential vault",
    icon: "key-round",
    group: "Access",
    description: "Device credentials and API tokens. Requires a second factor."
  }, {
    id: "smartsheet",
    name: "Capacity plan",
    icon: "table-2",
    group: "Planning",
    description: "Rolling twelve-month bandwidth and hardware plan."
  }, {
    id: "backups",
    name: "Config archive",
    icon: "hard-drive-download",
    group: "Source of truth",
    description: "Nightly device configuration snapshots and diffs."
  }],
  activity: [{
    who: "M. Ruiz",
    what: "submitted evidence for REP-2288",
    when: "14 min ago",
    icon: "file-check"
  }, {
    who: "Discovery job",
    what: "added 3 devices at PHX-01",
    when: "1 hr ago",
    icon: "refresh-cw"
  }, {
    who: "D. Okafor",
    what: "closed TSK-8837",
    when: "2 hr ago",
    icon: "circle-check"
  }, {
    who: "J. Lindqvist",
    what: "flagged config drift on chi01-sw-04",
    when: "4 hr ago",
    icon: "git-compare"
  }, {
    who: "System",
    what: "marked REP-2291 overdue",
    when: "Yesterday 17:00",
    icon: "triangle-alert"
  }]
};
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/nova-portal/data.js", error: String((e && e.message) || e) }); }

__ds_ns.Badge = __ds_scope.Badge;

__ds_ns.Button = __ds_scope.Button;

__ds_ns.Card = __ds_scope.Card;

__ds_ns.Icon = __ds_scope.Icon;

__ds_ns.IconButton = __ds_scope.IconButton;

__ds_ns.Tag = __ds_scope.Tag;

__ds_ns.DataTable = __ds_scope.DataTable;

__ds_ns.DONUT_TONES = __ds_scope.DONUT_TONES;

__ds_ns.DonutChart = __ds_scope.DonutChart;

__ds_ns.EmptyState = __ds_scope.EmptyState;

__ds_ns.MetricTile = __ds_scope.MetricTile;

__ds_ns.ProgressMeter = __ds_scope.ProgressMeter;

__ds_ns.StatusPill = __ds_scope.StatusPill;

__ds_ns.Alert = __ds_scope.Alert;

__ds_ns.Dialog = __ds_scope.Dialog;

__ds_ns.Toast = __ds_scope.Toast;

__ds_ns.Tooltip = __ds_scope.Tooltip;

__ds_ns.Checkbox = __ds_scope.Checkbox;

__ds_ns.FileUpload = __ds_scope.FileUpload;

__ds_ns.FormField = __ds_scope.FormField;

__ds_ns.Input = __ds_scope.Input;

__ds_ns.Radio = __ds_scope.Radio;

__ds_ns.Select = __ds_scope.Select;

__ds_ns.Switch = __ds_scope.Switch;

__ds_ns.Textarea = __ds_scope.Textarea;

__ds_ns.Breadcrumbs = __ds_scope.Breadcrumbs;

__ds_ns.Pagination = __ds_scope.Pagination;

__ds_ns.SidebarNav = __ds_scope.SidebarNav;

__ds_ns.Tabs = __ds_scope.Tabs;

})();
