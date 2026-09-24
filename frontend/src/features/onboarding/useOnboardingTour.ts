/**
 * First-run tutorial state: welcome → spotlight steps → done, with persistence.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useStore } from "../../state/store";
import { prepareShellForTour } from "./shellChrome";
import { SPOTLIGHT_STEPS, type TourPhase } from "./tourSteps";

const replayListeners = new Set<() => void>();

/** CommandBar calls this; the mounted tour subscribes. */
export function requestTourReplay(): void {
  for (const listener of replayListeners) listener();
}

export type OnboardingTourApi = {
  phase: TourPhase | null;
  stepIndex: number;
  stepCount: number;
  open: boolean;
  start: () => void;
  replay: () => void;
  skip: () => void;
  next: () => void;
  back: () => void;
  finish: () => void;
};

async function markComplete(): Promise<void> {
  await useStore.getState().applySettings({ onboarding_tour_completed: true });
}

function applyStepSideEffects(stepIndex: number): void {
  const step = SPOTLIGHT_STEPS[stepIndex];
  if (!step) return;

  if (step.mode) useStore.getState().setMode(step.mode);
  if (step.prepare) prepareShellForTour(step.prepare);

  if (step.openNoteTitle) {
    const note = useStore
      .getState()
      .tree?.notes.find((entry) => entry.title === step.openNoteTitle);
    if (note) void useStore.getState().openNoteById(note.id);
  }
}

export function useOnboardingTour(): OnboardingTourApi {
  const ready = useStore((s) => s.connection === "ready");
  const completed = useStore(
    (s) => s.settings?.onboarding_tour_completed ?? true,
  );
  const settingsLoaded = useStore((s) => s.settings !== null);

  const [phase, setPhase] = useState<TourPhase | null>(null);
  const [stepIndex, setStepIndex] = useState(0);
  const autoStarted = useRef(false);
  const stepRef = useRef(0);
  stepRef.current = stepIndex;

  const replay = useCallback(() => {
    setPhase("welcome");
    setStepIndex(0);
  }, []);

  const start = replay;

  const skip = useCallback(() => {
    setPhase(null);
    setStepIndex(0);
    void markComplete();
  }, []);

  const finish = useCallback(() => {
    setPhase(null);
    setStepIndex(0);
    void markComplete();
  }, []);

  const next = useCallback(() => {
    setPhase((current) => {
      if (current === "welcome") {
        setStepIndex(0);
        queueMicrotask(() => applyStepSideEffects(0));
        return "spotlight";
      }
      if (current === "spotlight") {
        const nextIndex = stepRef.current + 1;
        if (nextIndex >= SPOTLIGHT_STEPS.length) {
          return "done";
        }
        setStepIndex(nextIndex);
        queueMicrotask(() => applyStepSideEffects(nextIndex));
        return "spotlight";
      }
      if (current === "done") {
        queueMicrotask(() => {
          setPhase(null);
          setStepIndex(0);
          void markComplete();
        });
        return null;
      }
      return current;
    });
  }, []);

  const back = useCallback(() => {
    setPhase((current) => {
      if (current === "done") {
        const last = SPOTLIGHT_STEPS.length - 1;
        setStepIndex(last);
        queueMicrotask(() => applyStepSideEffects(last));
        return "spotlight";
      }
      if (current === "spotlight") {
        if (stepRef.current <= 0) {
          return "welcome";
        }
        const prev = stepRef.current - 1;
        setStepIndex(prev);
        queueMicrotask(() => applyStepSideEffects(prev));
        return "spotlight";
      }
      return current;
    });
  }, []);

  useEffect(() => {
    if (!ready || !settingsLoaded || autoStarted.current) return;
    if (completed) return;
    autoStarted.current = true;
    setPhase("welcome");
  }, [ready, settingsLoaded, completed]);

  useEffect(() => {
    const onReplay = (): void => {
      replay();
    };
    replayListeners.add(onReplay);
    return () => {
      replayListeners.delete(onReplay);
    };
  }, [replay]);

  return {
    phase,
    stepIndex,
    stepCount: SPOTLIGHT_STEPS.length,
    open: phase !== null,
    start,
    replay,
    skip,
    next,
    back,
    finish,
  };
}
