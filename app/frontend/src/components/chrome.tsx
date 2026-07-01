// Shared view chrome — header (breadcrumb + title + actions) and tab buttons.
// Ported from the design's ViewHeader / TabButton.
import { Fragment } from "react";
import type { ReactNode } from "react";
import { Icon } from "./Icon";

export interface Crumb {
  label: ReactNode;
  current?: boolean;
  onClick?: () => void;
}

export function ViewHeader({
  eyebrow,
  title,
  subtitle,
  actions,
  breadcrumb,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  breadcrumb?: Crumb[];
}) {
  return (
    <div className="view-header">
      {breadcrumb && (
        <div className="breadcrumb">
          {breadcrumb.map((b, i) => (
            <Fragment key={i}>
              {i > 0 && <span className="breadcrumb-sep">/</span>}
              {b.onClick ? (
                <button className="breadcrumb-link" onClick={b.onClick}>{b.label}</button>
              ) : (
                <span className={"breadcrumb-item" + (b.current ? " breadcrumb-item--current" : "")}>{b.label}</span>
              )}
            </Fragment>
          ))}
        </div>
      )}
      <div className="view-header-row">
        <div className="view-header-text">
          {eyebrow && <div className="view-eyebrow">{eyebrow}</div>}
          <h1 className="view-title">{title}</h1>
          {subtitle && <p className="view-subtitle">{subtitle}</p>}
        </div>
        {actions && <div className="view-actions">{actions}</div>}
      </div>
    </div>
  );
}

export function TabButton({
  active,
  onClick,
  icon,
  label,
  disabled,
}: {
  active: boolean;
  onClick: () => void;
  icon: string;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      className={"tab-btn" + (active ? " tab-btn--active" : "") + (disabled ? " tab-btn--disabled" : "")}
      onClick={!disabled ? onClick : undefined}
      disabled={disabled}
    >
      <Icon name={icon} />
      <span>{label}</span>
    </button>
  );
}

// A simple centered placeholder for views that are designed but not wired yet
// (Ideas, New Ideation). Keeps the chrome consistent while signalling status.
export function Placeholder({ title, note }: { title: string; note: string }) {
  return (
    <div className="view">
      <ViewHeader eyebrow="Coming soon" title={title} subtitle={note} />
      <div className="card card--wide" style={{ marginTop: 8 }}>
        <div className="card-label">Not yet wired</div>
        <p className="card-body">
          This surface is part of Phase 2–3 (idea generation &amp; refinement). The design is in
          place; the agent behind it is implemented in a later milestone.
        </p>
      </div>
    </div>
  );
}
