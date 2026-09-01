import type { Opportunity } from "../api/types";

/** Statuses where the money is still out and something may yet be done. */
const UNRESOLVED = new Set([
  "open",
  "decision_in_progress",
  "intervention_pending",
  "native_retry_pending",
  "shadow_observation",
]);

const CLOSING_SOON_MS = 6 * 60 * 60 * 1000;

export interface QueueLane {
  key: string;
  title: string;
  /** Why these are grouped, and what the operator is meant to do about them. */
  rationale: string;
  urgent: boolean;
  items: Opportunity[];
}

/**
 * Split unresolved opportunities into the three questions an operator actually
 * has, in priority order.
 *
 * Exported for testing: the ordering is the product decision here, so it is
 * worth pinning down independently of how it renders.
 */
export function buildLanes(opportunities: Opportunity[], now: number = Date.now()): QueueLane[] {
  const escalated: Opportunity[] = [];
  const closing: Opportunity[] = [];
  const undecided: Opportunity[] = [];
  const awaiting: Opportunity[] = [];
  const stale: Opportunity[] = [];

  for (const opp of opportunities) {
    if (opp.status === "escalated") {
      escalated.push(opp);
      continue;
    }
    if (!UNRESOLVED.has(opp.status)) continue;

    const endsAt = new Date(opp.window_ends_at).getTime();
    const remaining = Number.isFinite(endsAt) ? endsAt - now : Number.POSITIVE_INFINITY;
    if (remaining <= 0) {
      // The window has already closed. Acting is pointless, so this is not
      // urgent work — it is a sign the sweeper has not run.
      stale.push(opp);
    } else if (remaining <= CLOSING_SOON_MS) {
      closing.push(opp);
    } else if (opp.status === "open" || opp.status === "decision_in_progress") {
      undecided.push(opp);
    } else {
      // Acted on, now waiting on the customer. Not work, but not resolved
      // either -- and dropping it would make the queue quietly lose track of
      // most of the money that is still out.
      awaiting.push(opp);
    }
  }

  // Escalations rank by amount: they are waiting on a person, and the largest
  // is the most expensive minute of that person's attention.
  escalated.sort((a, b) => b.amount_minor - a.amount_minor);
  // Everything else ranks by what acting is actually worth.
  const byValue = (a: Opportunity, b: Opportunity) =>
    (b.expected_recovery_minor ?? 0) - (a.expected_recovery_minor ?? 0);
  closing.sort(byValue);
  undecided.sort(byValue);
  awaiting.sort(byValue);
  stale.sort((a, b) => b.amount_minor - a.amount_minor);

  return [
    {
      key: "escalated",
      title: "Needs a person",
      rationale:
        "Policy refused to act automatically — usually because the amount is above the value cap. These do not resolve on their own.",
      urgent: true,
      items: escalated,
    },
    {
      key: "closing",
      title: "Window closing",
      rationale: "The recovery window ends within six hours. After that the opportunity expires.",
      urgent: true,
      items: closing,
    },
    {
      key: "undecided",
      title: "Awaiting a decision",
      rationale: "In the pipeline, not yet acted on. No action is required unless they stall here.",
      urgent: false,
      items: undecided,
    },
    {
      key: "awaiting",
      title: "Awaiting an outcome",
      rationale:
        "An action has been taken and the recovery window is still open. Nothing to do until the customer acts or the window closes.",
      urgent: false,
      items: awaiting,
    },
    {
      key: "stale",
      title: "Window already closed",
      rationale:
        "Still marked open after their recovery window expired. Nothing can be recovered here — it means the sweeper has not run.",
      urgent: false,
      items: stale,
    },
  ];
}
