// RAT's preferences on the frontend side: kept at module level so every component (and the boot HUD, which runs with
// the panel closed) reads the same values. useStore() re-renders a component when they change.
import { useEffect, useState } from "react";
import type { Prefs } from "./api";

const listeners = new Set<() => void>();
let current: Prefs | null = null;

export const prefs = {
  get(): Prefs | null {
    return current;
  },
  set(p: Prefs) {
    current = p;
    listeners.forEach((l) => l());
  },
};

export function hintsOn(): boolean {
  return current?.show_hints ?? true;
}

export function usePrefs(): Prefs | null {
  const [, set] = useState(0);
  useEffect(() => {
    const l = () => set((n) => n + 1);
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  }, []);
  return current;
}
