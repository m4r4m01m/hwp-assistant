import { useCallback, useEffect, useState, type RefObject } from "react";

export function usePageVisible() {
  const [visible, setVisible] = useState(() => document.visibilityState !== "hidden");
  useEffect(() => {
    const changed = () => setVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", changed);
    return () => document.removeEventListener("visibilitychange", changed);
  }, []);
  return visible;
}

export function useDebouncedValue<T>(value: T, delay = 300): [T, () => void] {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  const flush = useCallback(() => setSettled(value), [value]);
  return [settled, flush];
}

// A mobile drawer covers the canvas without destroying its editor or unsaved
// state. Remove the covered canvas from keyboard navigation until it is visible.
export function useMobileDrawerCanvas(
  root: RefObject<HTMLElement | null>,
  canvasSelector: string,
  drawerSelector: string,
) {
  useEffect(() => {
    const host = root.current;
    if (!host) return;
    const narrow = window.matchMedia("(max-width: 720px)");
    const original = new Map<HTMLElement, boolean>();
    const update = () => {
      const covered = narrow.matches && !!host.querySelector(drawerSelector);
      for (const canvas of host.querySelectorAll<HTMLElement>(canvasSelector)) {
        if (!original.has(canvas)) original.set(canvas, canvas.inert);
        canvas.inert = covered || original.get(canvas)!;
      }
    };
    const observer = new MutationObserver(update);
    observer.observe(host, { subtree: true, childList: true, attributes: true, attributeFilter: ["hidden"] });
    narrow.addEventListener("change", update);
    update();
    return () => {
      observer.disconnect();
      narrow.removeEventListener("change", update);
      for (const [canvas, value] of original) canvas.inert = value;
    };
  }, [root, canvasSelector, drawerSelector]);
}
