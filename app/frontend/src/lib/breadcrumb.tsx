// The path trail lives in the top nav bar, not in each view's header — but the
// views are the only ones that know where they are. They publish their trail
// here through `usePublishBreadcrumb` (ViewHeader does it for them) and the
// topbar renders whatever the visible view last published.
import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

export interface Crumb {
  label: ReactNode;
  current?: boolean;
  onClick?: () => void;
}

const Ctx = createContext<{ crumbs: Crumb[]; setCrumbs: (c: Crumb[]) => void }>({
  crumbs: [],
  setCrumbs: () => {},
});

export function BreadcrumbProvider({ children }: { children: ReactNode }) {
  const [crumbs, setCrumbs] = useState<Crumb[]>([]);
  const value = useMemo(() => ({ crumbs, setCrumbs }), [crumbs]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useBreadcrumb() {
  return useContext(Ctx);
}

/** Publish this view's trail. Keyed on the labels so re-renders that only mint
 *  fresh click handlers do not loop through the provider's state. */
export function usePublishBreadcrumb(crumbs?: Crumb[]) {
  const { setCrumbs } = useBreadcrumb();
  const key = JSON.stringify((crumbs ?? []).map((c) => (typeof c.label === "string" ? c.label : "·")));
  useEffect(() => {
    setCrumbs(crumbs ?? []);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
}
