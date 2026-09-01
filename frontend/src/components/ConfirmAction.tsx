import { useEffect, useRef } from "react";

export interface ConfirmFact {
  label: string;
  value: string;
  /** Marks the fact that makes this action consequential. */
  emphasis?: boolean;
}

interface ConfirmActionProps {
  title: string;
  /** One sentence: what will happen, in the operator's language. */
  summary: string;
  facts: ConfirmFact[];
  confirmLabel: string;
  /** Set when the action can reach a real customer or change live behaviour. */
  danger?: boolean;
  /** Overrides the impact chip when "affects customers" is not what is at stake. */
  impactLabel?: string;
  /** Shown as a standing reassurance when nothing can leave the building. */
  safeNote?: string | null;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * A preflight for actions that spend money, contact people, or change what the
 * runtime does.
 *
 * The rule this enforces: before an operator commits, they can see the scope
 * (how many, how much), the execution mode (shadow or live), and what cannot be
 * undone. Batch incident response, model promotion and manual re-decide all
 * used to fire on a single unconfirmed click.
 */
export function ConfirmAction({
  title, summary, facts, confirmLabel, danger = false, impactLabel, safeNote = null,
  busy = false, onConfirm, onCancel,
}: ConfirmActionProps) {
  const confirmRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    openerRef.current = document.activeElement as HTMLElement | null;
    confirmRef.current?.focus();
    return () => openerRef.current?.focus?.();
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        onCancel();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = dialogRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [onCancel]);

  return (
    <div className="modal-overlay" onMouseDown={onCancel}>
      <div
        ref={dialogRef}
        className="modal-content confirm-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-action-title"
        aria-describedby="confirm-action-summary"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="confirm-head">
          <h2 id="confirm-action-title">{title}</h2>
          <span className={`chip ${danger ? "chip-amber" : "chip-neutral"}`}>
            {impactLabel ?? (danger ? "Affects customers" : "Analysis only")}
          </span>
        </header>

        <p id="confirm-action-summary" className="confirm-summary">{summary}</p>

        <dl className="confirm-facts">
          {facts.map((fact) => (
            <div key={fact.label} className={fact.emphasis ? "confirm-fact is-emphasis" : "confirm-fact"}>
              <dt>{fact.label}</dt>
              <dd>{fact.value}</dd>
            </div>
          ))}
        </dl>

        {safeNote && <p className="confirm-safe-note">{safeNote}</p>}

        <footer className="confirm-actions">
          <button type="button" className="btn btn-secondary" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            ref={confirmRef}
            type="button"
            className={`btn ${danger ? "btn-danger" : "btn-primary"}`}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? "Working…" : confirmLabel}
          </button>
        </footer>
      </div>
    </div>
  );
}
