/**
 * Reduced motion, resolved from both sources of truth.
 *
 * The OS preference and the in-app setting are different questions: "I always
 * want less motion everywhere" and "I want less motion in Strata". The app
 * setting wins when it is explicit; otherwise the OS decides. This hook is what
 * the WebGL scene reads — CSS cannot reach the canvas.
 */

import { useEffect, useState } from "react";
import { useStore } from "../state/store";

const QUERY = "(prefers-reduced-motion: reduce)";

export function useReducedMotion(): boolean {
  const preference = useStore((state) => state.settings?.motion ?? "system");
  const [systemPrefersReduced, setSystemPrefersReduced] = useState<boolean>(
    () =>
      typeof window !== "undefined" && typeof window.matchMedia === "function"
        ? window.matchMedia(QUERY).matches
        : false,
  );

  useEffect(() => {
    if (
      typeof window === "undefined" ||
      typeof window.matchMedia !== "function"
    )
      return;
    const media = window.matchMedia(QUERY);
    const listener = (event: MediaQueryListEvent): void =>
      setSystemPrefersReduced(event.matches);
    media.addEventListener("change", listener);
    return () => media.removeEventListener("change", listener);
  }, []);

  if (preference === "reduced") return true;
  if (preference === "full") return false;
  return systemPrefersReduced;
}
