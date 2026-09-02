import type { TabKey } from "../components/Header";

export interface TourStep {
  tab: TabKey | null;
  /** CSS selector for the element this step should sit next to. Null centres the box. */
  target: string | null;
  title: string;
  body: string;
}

const STORAGE_KEY = "recover_tour_seen";

export function hasTourBeenSeen(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function markTourSeen(): void {
  try {
    localStorage.setItem(STORAGE_KEY, "1");
  } catch {
    // Private browsing or storage disabled -- the tour just replays next visit.
  }
}

export const TOUR_STEPS: TourStep[] = [
  {
    tab: null,
    target: ".brand-group",
    title: "What is Recover?",
    body: "Recover reads the Razorpay webhooks a merchant already sends, scores every failed payment, and decides whether chasing it is worth the cost of contacting the customer. This tour walks through what each part of the console is actually showing you.",
  },
  {
    tab: "overview",
    target: ".sidebar-status",
    title: "Mode and environment",
    body: "The environment (Razorpay test mode here — never live money) and the execution mode. \"Observe only\" means the agent scores and decides, but never actually contacts a customer — use the \"Arm live\" link on the Execution row to change that. The queue count is how many opportunities are still waiting. \"EV gate ₹20\" is the minimum net expected value an attempt must clear to be worth acting on; \"3-contact cap\" is the most times the system will ever contact one customer about the same failure.",
  },
  {
    tab: "overview",
    target: ".kpi-card.is-hero",
    title: "Revenue at risk & Net recovered today",
    body: "\"Revenue at risk\" is every currently-unresolved failed payment, regardless of when it happened. \"Net recovered today\" is strictly scoped to the current calendar day (UTC) — it can legitimately show ₹0 on a quiet day even though the historical numbers are populated. That's by design, not a bug.",
  },
  {
    tab: "overview",
    target: ".kpi-card.is-sage",
    title: "Payment success & Signed webhooks",
    body: "Payment success is the real-time conversion rate across today's attempts. Signed webhooks counts verified, deduplicated events Razorpay has actually delivered — a single checkout can fire several (authorized, captured, order.paid), so this is often higher than the number of payments you'd expect.",
  },
  {
    tab: "overview",
    target: ".kpi-band-head",
    title: "Demo data vs. real traffic",
    body: "This install is seeded with synthetic historical data so the dashboard isn't empty on day one — every seeded record is flagged is_synthetic and excluded from real KPIs by default. Toggle to see the full picture, or the honest real-only view.",
  },
  {
    tab: "overview",
    target: ".feed-panel",
    title: "Activity stream",
    body: "A live, deduplicated feed of every webhook and decision as it happens — payments, opportunities, policy checks, verified outcomes. It replays recent history on load so it's never blank, then streams new events in real time.",
  },
  {
    tab: "overview",
    target: ".feed-panel",
    title: "How recovery works",
    body: "Every failure goes through six steps: Observe (verify the webhook), Diagnose (classify the failure type), Decide (score against an ML model), Protect (enforce value caps and contact limits), Act (send a recovery link, or don't), Verify (reconcile the real outcome and separate natural recovery from what Recover actually caused).",
  },
  {
    tab: "opportunities",
    target: ".queue-lanes",
    title: "Recovery queue",
    body: "What needs attention, ranked by what acting is worth. \"Needs a person\" is anything above the automated value cap — policy refuses to decide those on its own. \"Awaiting an outcome\" is everything already acted on, waiting for the customer or the recovery window to close.",
  },
  {
    tab: "razorpay",
    target: ".razorpay-container",
    title: "Razorpay state",
    body: "Reconciled state pulled straight from Razorpay — orders, payment links, subscriptions, invoices, downtime, and revenue adjustments. This is the source of truth the rest of the app reasons about, kept accurate even if a webhook gets missed.",
  },
  {
    tab: "incidents",
    target: ".incidents-container",
    title: "Incidents",
    body: "Automated detection of method- or bank-level outages — when a whole payment rail starts failing at once, this is where the coordinated response (and its audit trail) lives.",
  },
  {
    tab: "simulation",
    target: ".simulation-layout",
    title: "Demo traffic",
    body: "Generate isolated synthetic scenarios on demand — useful for testing or demonstrating specific failure patterns without touching real evidence or live experiment data.",
  },
  {
    tab: "governance",
    target: ".governance-container",
    title: "Evidence",
    body: "The causal proof: a holdout group is never touched, so the gap between treatment and control is the only recovery Recover is willing to claim. Also covers the model promotion gate and the hard execution guardrails enforced on every decision.",
  },
];
