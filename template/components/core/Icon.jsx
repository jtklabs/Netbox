import React from "react";

const pascal = (s) => String(s || "").replace(/(^|[-_ ])(\w)/g, (_, __, c) => c.toUpperCase());

/* Renders a Lucide glyph. Lucide is loaded from CDN by the host page
   (<script src="https://unpkg.com/lucide@0.469.0/dist/umd/lucide.js">);
   this component reads the icon data off window.lucide and draws it as React. */
export function Icon({ name, size = 16, strokeWidth = 1.75, color = "currentColor", label, style, ...rest }) {
  const lib = (typeof window !== "undefined" && window.lucide && window.lucide.icons) || null;
  const node = lib ? lib[pascal(name)] || lib[name] : null;
  let children = [];
  if (Array.isArray(node)) children = node[0] === "svg" ? node[2] || [] : node;
  return React.createElement(
    "svg",
    {
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
      style: { display: "block", flex: "0 0 auto", ...style },
      ...rest,
    },
    children.map((child, i) => {
      const tag = Array.isArray(child) ? child[0] : null;
      const attrs = Array.isArray(child) ? child[1] || {} : {};
      return tag ? React.createElement(tag, { key: i, ...attrs }) : null;
    })
  );
}
