import React from "react";
import { Icon } from "../core/Icon.jsx";

export function Breadcrumbs({ items = [], onNavigate, style, ...rest }) {
  return (
    <nav aria-label="Breadcrumb" style={{ minWidth: 0, ...style }} {...rest}>
      <ol style={{ display: "flex", alignItems: "center", gap: "var(--space-1)", listStyle: "none", margin: 0, padding: 0, flexWrap: "wrap" }}>
        {items.map((item, i) => {
          const last = i === items.length - 1;
          return (
            <li key={item.label + i} style={{ display: "flex", alignItems: "center", gap: "var(--space-1)", minWidth: 0 }}>
              {last ? (
                <span aria-current="page" style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)", fontWeight: "var(--weight-medium)" }}>{item.label}</span>
              ) : (
                <a
                  href={item.href || "#"}
                  onClick={(e) => { e.preventDefault(); onNavigate && onNavigate(item.id || item.label); }}
                  style={{ fontSize: "var(--text-xs)", color: "var(--text-link)", textDecoration: "none", borderBottom: 0 }}
                >
                  {item.label}
                </a>
              )}
              {!last ? <Icon name="chevron-right" size={12} color="var(--text-subtle)" /> : null}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
