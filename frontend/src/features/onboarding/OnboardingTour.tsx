/**
 * Hybrid first-run tutorial: welcome dialog, spotlight coach marks, done card.
 */

import { useEffect, useId, useLayoutEffect, useState } from "react";
import { createPortal } from "react-dom";
import { SPOTLIGHT_STEPS } from "./tourSteps";
import { useOnboardingTour } from "./useOnboardingTour";
import "./onboarding.css";

const PAD = 8;

type Rect = { top: number; left: number; width: number; height: number };

function measureTarget(name: string): Rect | null {
  const el = document.querySelector<HTMLElement>(`[data-tour="${name}"]`);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  if (r.width < 2 && r.height < 2) return null;
  return {
    top: Math.max(0, r.top - PAD),
    left: Math.max(0, r.left - PAD),
    width: Math.min(window.innerWidth, r.width + PAD * 2),
    height: Math.min(window.innerHeight, r.height + PAD * 2),
  };
}

function cardPosition(hole: Rect | null): { top: number; left: number } {
  const cardW = Math.min(360, window.innerWidth - 32);
  const cardH = 200;
  if (!hole) {
    return {
      top: Math.max(24, (window.innerHeight - cardH) / 2),
      left: Math.max(16, (window.innerWidth - cardW) / 2),
    };
  }
  const below = hole.top + hole.height + 12;
  const above = hole.top - cardH - 12;
  const top =
    below + cardH < window.innerHeight - 16
      ? below
      : above > 16
        ? above
        : Math.max(16, window.innerHeight - cardH - 16);
  const left = Math.min(
    Math.max(16, hole.left),
    window.innerWidth - cardW - 16,
  );
  return { top, left };
}

export function OnboardingTour(): JSX.Element | null {
  const tour = useOnboardingTour();
  const titleId = useId();
  const [hole, setHole] = useState<Rect | null>(null);
  const [cardPos, setCardPos] = useState({ top: 80, left: 24 });

  useLayoutEffect(() => {
    if (tour.phase !== "spotlight") {
      setHole(null);
      return;
    }
    const step = SPOTLIGHT_STEPS[tour.stepIndex];
    if (!step) return;

    const update = (): void => {
      const next = measureTarget(step.target);
      setHole(next);
      setCardPos(cardPosition(next));
    };
    update();
    const t1 = window.setTimeout(update, 50);
    const t2 = window.setTimeout(update, 200);
    window.addEventListener("resize", update);
    return () => {
      window.clearTimeout(t1);
      window.clearTimeout(t2);
      window.removeEventListener("resize", update);
    };
  }, [tour.phase, tour.stepIndex]);

  useEffect(() => {
    if (!tour.open) return;
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        event.preventDefault();
        tour.skip();
        return;
      }
      if (event.key === "ArrowRight") {
        event.preventDefault();
        tour.next();
        return;
      }
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        tour.back();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [tour]);

  if (!tour.open || tour.phase === null) return null;

  if (tour.phase === "welcome") {
    return createPortal(
      <div
        className="dialog-backdrop tour-welcome"
        role="presentation"
        onClick={(event) => {
          if (event.target === event.currentTarget) tour.skip();
        }}
      >
        <div
          className="dialog"
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
        >
          <p className="tour-welcome__brand">Strata</p>
          <h2 id={titleId} className="dialog__title">
            Welcome to your workspace
          </h2>
          <div className="dialog__body">
            <p>
              A short tour of the shell: modes, capture, layers, writing, the
              graph, and how AI only sees what you select.
            </p>
            <p>
              Skip anytime with Escape. You can replay later from{" "}
              <strong>More → Tutorial</strong>.
            </p>
          </div>
          <div className="dialog__actions">
            <button
              type="button"
              className="button button--ghost"
              onClick={tour.skip}
            >
              Skip
            </button>
            <button
              type="button"
              className="button button--primary"
              autoFocus
              onClick={tour.next}
            >
              Start tour
            </button>
          </div>
        </div>
      </div>,
      document.body,
    );
  }

  if (tour.phase === "done") {
    return createPortal(
      <div
        className="dialog-backdrop tour-done"
        role="presentation"
        onClick={(event) => {
          if (event.target === event.currentTarget) tour.finish();
        }}
      >
        <div
          className="dialog"
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
        >
          <h2 id={titleId} className="dialog__title">
            You are set
          </h2>
          <div className="dialog__body">
            <p>
              Open the <strong>Start Here</strong> note in Files for a deeper
              walkthrough of the demo workspace. Replay this tour anytime from{" "}
              <strong>More → Tutorial</strong>.
            </p>
          </div>
          <div className="dialog__actions">
            <button
              type="button"
              className="button button--ghost"
              onClick={tour.back}
            >
              Back
            </button>
            <button
              type="button"
              className="button button--primary"
              autoFocus
              onClick={tour.finish}
            >
              Finish
            </button>
          </div>
        </div>
      </div>,
      document.body,
    );
  }

  const step = SPOTLIGHT_STEPS[tour.stepIndex];
  if (!step) return null;

  return createPortal(
    <>
      <div className="tour-spotlight" aria-hidden="true">
        {hole ? (
          <div
            className="tour-spotlight__hole"
            style={{
              top: hole.top,
              left: hole.left,
              width: hole.width,
              height: hole.height,
            }}
          />
        ) : (
          <div className="tour-spotlight__mask" />
        )}
      </div>
      <div
        className="tour-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        style={{ top: cardPos.top, left: cardPos.left }}
      >
        <p className="tour-card__progress mono">
          Step {tour.stepIndex + 1} of {tour.stepCount}
        </p>
        <h2 id={titleId} className="tour-card__title">
          {step.title}
        </h2>
        <p className="tour-card__body">{step.body}</p>
        <div className="tour-card__actions">
          <button
            type="button"
            className="button button--ghost"
            onClick={tour.skip}
          >
            Skip
          </button>
          <button
            type="button"
            className="button button--ghost"
            onClick={tour.back}
          >
            Back
          </button>
          <button
            type="button"
            className="button button--primary"
            autoFocus
            onClick={tour.next}
          >
            {tour.stepIndex + 1 >= tour.stepCount ? "Continue" : "Next"}
          </button>
        </div>
      </div>
    </>,
    document.body,
  );
}
