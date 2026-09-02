import { useEffect, useRef, useState } from "react";
import type { TabKey } from "./Header";
import { Icon } from "./Icon";
import { markTourSeen, TOUR_STEPS } from "../utils/tourSteps";

const BOX_WIDTH = 320;
const MARGIN = 14;

interface BoxPosition {
  top: number;
  left: number;
}

function computePosition(rect: DOMRect, boxHeight: number): BoxPosition {
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  let left = rect.right + MARGIN;
  let top = rect.top;

  if (left + BOX_WIDTH + MARGIN > vw) {
    left = rect.left - BOX_WIDTH - MARGIN;
  }
  if (left < MARGIN) {
    left = Math.min(Math.max(rect.left, MARGIN), vw - BOX_WIDTH - MARGIN);
    top = rect.bottom + MARGIN;
  }

  top = Math.min(Math.max(top, MARGIN), vh - boxHeight - MARGIN);
  left = Math.min(Math.max(left, MARGIN), vw - BOX_WIDTH - MARGIN);
  return { top, left };
}

interface GuidedTourProps {
  onClose: () => void;
  onNavigateTab: (tab: TabKey) => void;
}

export function GuidedTour({ onClose, onNavigateTab }: GuidedTourProps) {
  const [index, setIndex] = useState(0);
  const [position, setPosition] = useState<BoxPosition | null>(null);
  const boxRef = useRef<HTMLElement>(null);
  const highlightedRef = useRef<Element | null>(null);
  const step = TOUR_STEPS[index];
  const isLast = index === TOUR_STEPS.length - 1;
  const isFirst = index === 0;

  useEffect(() => {
    if (step.tab) onNavigateTab(step.tab);

    if (highlightedRef.current) {
      highlightedRef.current.classList.remove("tour-target-highlight");
      highlightedRef.current = null;
    }

    let cancelled = false;
    let attempts = 0;

    function place() {
      if (cancelled) return;
      const target = step.target ? document.querySelector(step.target) : null;
      const boxHeight = boxRef.current?.offsetHeight ?? 220;

      if (target) {
        target.classList.add("tour-target-highlight");
        highlightedRef.current = target;
        setPosition(computePosition(target.getBoundingClientRect(), boxHeight));
        return;
      }
      if (attempts < 10) {
        attempts += 1;
        requestAnimationFrame(place);
      } else {
        // Target never showed up -- centre the box rather than leave it stuck top-left.
        setPosition({
          top: window.innerHeight / 2 - boxHeight / 2,
          left: window.innerWidth / 2 - BOX_WIDTH / 2,
        });
      }
    }
    requestAnimationFrame(place);

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index]);

  useEffect(() => {
    return () => {
      if (highlightedRef.current) highlightedRef.current.classList.remove("tour-target-highlight");
    };
  }, []);

  useEffect(() => {
    function onResize() {
      const target = step.target ? document.querySelector(step.target) : null;
      const boxHeight = boxRef.current?.offsetHeight ?? 220;
      if (target) setPosition(computePosition(target.getBoundingClientRect(), boxHeight));
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [step.target]);

  function finish() {
    if (highlightedRef.current) highlightedRef.current.classList.remove("tour-target-highlight");
    markTourSeen();
    onClose();
  }

  return (
    <div className="tour-click-catcher" role="presentation" onMouseDown={finish}>
      <section
        ref={boxRef}
        className="tour-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="tour-title"
        style={position ? { top: position.top, left: position.left, visibility: "visible" } : { visibility: "hidden" }}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="tour-head">
          <span className="tour-step-count">{index + 1} / {TOUR_STEPS.length}</span>
          <button type="button" className="modal-close" onClick={finish} aria-label="Close tour">
            <Icon name="x" size={16} />
          </button>
        </div>
        <h2 id="tour-title" className="tour-title">{step.title}</h2>
        <p className="tour-body">{step.body}</p>
        <div className="tour-progress">
          {TOUR_STEPS.map((s, i) => (
            <span key={s.title} className={`tour-dot ${i === index ? "is-active" : ""}`} />
          ))}
        </div>
        <div className="tour-actions">
          <button type="button" className="btn btn-secondary btn-sm" onClick={finish}>
            Skip
          </button>
          <div className="tour-actions-nav">
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => setIndex((i) => Math.max(0, i - 1))}
              disabled={isFirst}
            >
              Back
            </button>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={() => (isLast ? finish() : setIndex((i) => i + 1))}
            >
              {isLast ? "Done" : "Next"}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}
