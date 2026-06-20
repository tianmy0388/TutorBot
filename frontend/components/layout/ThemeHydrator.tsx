"use client";

/**
 * ThemeHydrator — small client-only component that hydrates the theme
 * from localStorage on first render. Kept in its own component so the
 * server-rendered HTML stays small.
 */

import { useEffect } from "react";
import { useTutorStore } from "@/lib/store";

export function ThemeHydrator() {
  const hydrate = useTutorStore((s) => s.hydrateTheme);
  useEffect(() => {
    hydrate();
  }, [hydrate]);
  return null;
}
