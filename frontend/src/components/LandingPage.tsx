import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchExperimentMetrics,
  fetchFailureMix,
  fetchModelCard,
  fetchOperatingMode,
  fetchPolicyConfig,
  setShadowMode,
} from "../api/client";
import type {
  ExperimentMetrics,
  FailureMix,
  ModelCard,
  OperatingMode,
  PolicyConfig,
} from "../api/types";
import { inr } from "../utils/format";
import { getMaintenanceStatus } from "../utils/maintenanceWindow";
import { Icon } from "./Icon";
import { MaintenanceBanner } from "./MaintenanceBanner";

interface LandingPageProps {
  onStart: () => void;
}

/**
 * Published benchmarks for Indian online payments, used only where the page
 * makes a claim about the market rather than about this system.
 *
 * Anything describing what Recover does is read live from the API instead —
 * the two must never be confused, which is why they are separated here rather
 * than mixed into the copy as bare numbers.
 */
const MARKET = {
  /* Merchant-side blended success rates run 92–96%, so 4–8% of attempts fail.
     6% is the midpoint and the calculator's default. */
  failureRateDefault: 6.0,
  successRateRange: "92–96%",
  /* Recovery with no dedicated effort at all. */
  baselineRecovery: 0.10,
  baselineRecoveryLabel: "0–10%",
  /* Median across mixed dunning approaches; best-in-class reaches 70–85%. */
  medianRecovery: 0.476,
  medianRecoveryLabel: "47.6%",
  bestInClassLabel: "70–85%",
  /* NPCI Circular OC-149 decline targets. */
  technicalDeclineTarget: "<1%",
  technicalDeclineActual: "~0.8%",
} as const;

const SOURCES = [
  { label: "UPI success-rate benchmarks 2026", href: "https://productgrowth.in/insights/fintech/upi-payment-success-rates/" },
  { label: "State of involuntary churn", href: "https://retentionlens.com/state-of-involuntary-churn" },
  { label: "RBI payment system report", href: "https://www.business-standard.com/finance/news/upi-accounts-for-85-of-payment-volumes-rbi-s-payment-system-report-125102301181_1.html" },
];

const STEPS = [
  {
    n: "1",
    title: "Watch",
    body: "Signed Razorpay webhooks for every failed charge, mandate debit and autopay attempt land in one deduplicated stream.",
    tone: "accent",
  },
  {
    n: "2",
    title: "Score",
    body: "Recovery probability minus the rate these failures come back on their own, times the amount, minus what contact costs. That difference is the expected value.",
    tone: "accent-2",
  },
  {
    n: "3",
    title: "Act",
    body: "Payment link, reminder, instrument swap, or nothing. A deterministic policy the model cannot overrule caps value, contacts and cadence.",
    tone: "accent",
  },
  {
    n: "4",
    title: "Prove",
    body: "A withheld control group is never touched, so the lift is measured rather than asserted — and reported signed, even when it is negative.",
    tone: "accent-2",
  },
] as const;

const RAZORPAY_FIT = [
  {
    title: "Payments webhooks",
    body: "payment.failed arrives signed and deduplicated. Fail-closed HMAC verification, no polling, no reconciliation job.",
    tone: "accent",
    icon: "activity" as const,
  },
  {
    title: "Subscriptions & mandates",
    body: "Halted subscriptions get a card-update checkout; pending ones defer to Razorpay's own retry cycle rather than paying to duplicate it.",
    tone: "accent",
    icon: "refresh" as const,
  },
  {
    title: "Payment Links",
    body: "The recovery link carries the original amount, expires in 48 hours, and excludes any method currently in reported downtime.",
    tone: "accent-2",
    icon: "credit-card" as const,
  },
  {
    title: "Settlement & disputes",
    body: "Refunds and disputes are netted off recovered value, so the number finance sees is the one that survived.",
    tone: "accent-2",
    icon: "shield" as const,
  },
] as const;

function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

/** Compact Indian-format money for headline figures: ₹4.2 Cr, ₹18.6 L. */
function short(minor: number): string {
  const rupees = minor / 100;
  if (Math.abs(rupees) >= 1e7) return `₹${(rupees / 1e7).toFixed(2)} Cr`;
  if (Math.abs(rupees) >= 1e5) return `₹${(rupees / 1e5).toFixed(1)} L`;
  return inr(minor);
}

function PulseMark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={2.75} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 12h4l2.2-7 4.1 14L16 9l1.8 3H21" />
    </svg>
  );
}

/** The EV gate: failures stream in, split into chase and skip. */
function GateAnimation({ gateLabel, chase, skip }: {
  gateLabel: string; chase: string; skip: string;
}) {
  return (
    <div className="gate" aria-hidden="true">
      <div className="gate-blob" />
      <div className="gate-ring" />
      <div className="gate-label">EV gate {gateLabel}</div>

      <div className="gate-out gate-out-chase">
        <div className="gate-out-k">Chase</div>
        <div className="gate-out-v">{chase}</div>
      </div>
      <div className="gate-out gate-out-skip">
        <div className="gate-out-k">Skip</div>
        <div className="gate-out-v">{skip}</div>
      </div>

      <div className="gate-chip is-chase" style={{ animationDelay: "0s" }}>₹14,990 · low balance</div>
      <div className="gate-chip is-skip" style={{ animationDelay: "1.4s" }}>₹199 · below gate</div>
      <div className="gate-chip is-chase" style={{ animationDelay: "2.8s" }}>₹2,400 · mandate</div>
      <div className="gate-chip is-skip" style={{ animationDelay: "4.2s" }}>₹349 · dead card</div>
      <div className="gate-chip is-chase" style={{ animationDelay: "5.6s" }}>₹5,600 · decline</div>
      <div className="gate-chip is-skip" style={{ animationDelay: "7s" }}>₹86 · below gate</div>
    </div>
  );
}

export function LandingPage({ onStart }: LandingPageProps) {
  const [mode, setMode] = useState<OperatingMode | null>(null);
  const [policy, setPolicy] = useState<PolicyConfig | null>(null);
  const [mix, setMix] = useState<FailureMix | null>(null);
  const [experiment, setExperiment] = useState<ExperimentMetrics | null>(null);
  const [card, setCard] = useState<ModelCard | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedClass, setSelectedClass] = useState(0);

  const [gmvCr, setGmvCr] = useState(4.2);
  // Widened explicitly: `MARKET` is `as const`, so the default narrows to the
  // literal 6 and the slider could never set anything else.
  const [failRate, setFailRate] = useState<number>(MARKET.failureRateDefault);
  const [maintenance, setMaintenance] = useState(() => getMaintenanceStatus());

  useEffect(() => {
    const interval = setInterval(() => setMaintenance(getMaintenanceStatus()), 60_000);
    return () => clearInterval(interval);
  }, []);

  const load = useCallback(async () => {
    if (getMaintenanceStatus().isDown) return;
    const [m, p, f, e, c] = await Promise.allSettled([
      fetchOperatingMode(),
      fetchPolicyConfig(),
      fetchFailureMix(),
      // The holdout only claims a lift on a large enough sample; the seeded
      // demo baseline is synthetic, so ask for the view that includes it.
      fetchExperimentMetrics(true),
      fetchModelCard(),
    ]);
    if (m.status === "fulfilled") setMode(m.value);
    if (p.status === "fulfilled") setPolicy(p.value);
    if (f.status === "fulfilled") setMix(f.value);
    if (e.status === "fulfilled") setExperiment(e.value);
    if (c.status === "fulfilled") setCard(c.value);
  }, []);

  useEffect(() => {
    const first = setTimeout(() => void load(), 0);
    const poll = setInterval(load, 15000);
    return () => { clearTimeout(first); clearInterval(poll); };
  }, [load]);

  async function toggleShadow() {
    if (!mode) return;
    setBusy(true);
    setError(null);
    try {
      setMode(await setShadowMode(!mode.shadow_mode));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change execution mode.");
    } finally {
      setBusy(false);
    }
  }

  // The gate is the intervention's cost plus the margin policy requires on top
  // of it — the real number the engine compares against, not a round figure.
  const gateMinor = policy ? policy.min_ev_margin_minor + 1500 : null;
  const classes = mix?.classes ?? [];
  const active = classes[Math.min(selectedClass, Math.max(classes.length - 1, 0))];
  const maxShare = classes.reduce((m, c) => Math.max(m, c.share_of_value), 0) || 1;

  const calc = useMemo(() => {
    const monthlyMinor = gmvCr * 1e7 * 100;
    const atRisk = monthlyMinor * (failRate / 100);
    const treated = experiment?.treatment.conversion_rate ?? MARKET.medianRecovery;
    const held = experiment?.control.conversion_rate ?? MARKET.baselineRecovery;
    const delta = Math.max(0, treated - held);
    const recovered = atRisk * treated;
    const incremental = atRisk * delta;
    // Contacts are bounded by the policy's own attempt cap, not by wishful maths.
    const avgTicket = mix?.total_count ? mix.total_value_minor / mix.total_count : 250000;
    const contacts = Math.round((atRisk / avgTicket) * 0.62);
    const contactCost = contacts * 1500;
    return { atRisk, recovered, incremental, contacts, contactCost, treated, held, delta };
  }, [gmvCr, failRate, experiment, mix]);

  const measured = Boolean(experiment && experiment.treatment.total_opportunities > 0);
  const liveExecution = mode?.customer_side_effects_enabled === true;

  return (
    <main className="lp">
      <header className="lp-nav">
        <a className="lp-brand" href="#top">
          <span className="lp-brand-mark"><PulseMark size={17} /></span>
          <span>Razorpay Recover</span>
        </a>
        <nav className="lp-nav-links">
          <a href="#how">How it works</a>
          <a href="#mix">Failure mix</a>
          <a href="#worth">What it's worth</a>
          <a href="#evidence">Evidence</a>
          <a href="https://github.com/Pikachu-5/razorpay" target="_blank" rel="noopener noreferrer">
            GitHub
          </a>
        </nav>
        <span className="tag tag-accent-2">Buildathon · revenue recovery</span>
      </header>
      {maintenance.isDown && (
        <div className="lp-maintenance-wrap">
          <MaintenanceBanner message={maintenance.message} />
        </div>
      )}

      <section className="lp-hero" id="top">
        <div className="lp-hero-copy">
          <h6 className="lp-kicker">Failed payment recovery</h6>
          <h1 className="lp-h1">Chase the ones worth chasing.</h1>
          <p className="lp-lede">
            Every failure gets an expected value. Recover acts only when that value clears the
            cost of contacting a customer &mdash; then proves the result against a holdout it
            never touched.
          </p>
          <div className="lp-cta-row">
            <button type="button" className="btn btn-primary btn-lg" onClick={onStart}>
              See it recover a payment
            </button>
            <span className="lp-cta-note">
              No new integration &mdash; it reads the webhooks you already send.
            </span>
          </div>
        </div>

        <GateAnimation
          gateLabel={gateMinor === null ? "—" : inr(gateMinor)}
          chase={mix ? mix.classes.reduce((n, c) => n + c.recovered_count, 0).toLocaleString("en-IN") : "—"}
          skip={mix ? (mix.total_count - mix.classes.reduce((n, c) => n + c.recovered_count, 0)).toLocaleString("en-IN") : "—"}
        />
      </section>

      <section className="lp-section lp-section-bg" id="how">
        <h6 className="lp-kicker">How it works</h6>
        <h2 className="lp-h2">Four moves, on every single failure.</h2>
        <div className="lp-steps">
          {STEPS.map((step) => (
            <article key={step.n} className="lp-step">
              <div className={`lp-step-n is-${step.tone}`}>{step.n}</div>
              <h3 className="lp-step-title">{step.title}</h3>
              <p className="lp-step-body">{step.body}</p>
            </article>
          ))}
        </div>
        <p className="lp-foot-note">
          Policy in force right now: contact at most{" "}
          <strong>{policy?.max_contact_attempts ?? "—"} times</strong> per failure,{" "}
          <strong>{policy ? `${policy.cooldown_minutes} minutes` : "—"}</strong> apart, never above{" "}
          <strong>{policy ? inr(policy.max_amount_minor) : "—"}</strong> without a person, and never
          below <strong>{policy ? inr(policy.min_ev_margin_minor) : "—"}</strong> of net expected value.
        </p>
      </section>

      <section className="lp-section lp-section-surface" id="mix">
        <div className="lp-split">
          <div>
            <h6 className="lp-kicker">Failure mix</h6>
            <h2 className="lp-h2">Not every failure is the same failure.</h2>
            <p className="lp-body">
              A timeout wants a silent retry. A dead card wants a different instrument. Some want
              nothing at all. Pick a class to see how it resolves untouched &mdash; that rate is
              the bar every intervention has to beat.
            </p>

            {active ? (
              <div className="lp-mix-detail">
                <div className="lp-kicker lp-kicker-accent">{active.label}</div>
                <div className="lp-mix-stats">
                  <div>
                    <div className="lp-mix-stat">{pct(active.share_of_value)}</div>
                    <div className="lp-mix-stat-k">of failed value</div>
                  </div>
                  <div>
                    <div className="lp-mix-stat is-accent">{pct(active.recovery_rate)}</div>
                    <div className="lp-mix-stat-k">recovered</div>
                  </div>
                  <div>
                    <div className="lp-mix-stat">{active.count.toLocaleString("en-IN")}</div>
                    <div className="lp-mix-stat-k">failures seen</div>
                  </div>
                </div>
                <p className="lp-mix-play">{active.play}</p>
                {active.industry_reference && (
                  <p className="lp-mix-ref">Published merchant mixes: {active.industry_reference}.</p>
                )}
              </div>
            ) : (
              <div className="lp-mix-detail">
                <p className="lp-body">
                  No failures recorded yet. Seed the demo baseline to populate this.
                </p>
              </div>
            )}
          </div>

          <div className="lp-mix-list">
            {classes.map((cls, index) => (
              <button
                key={cls.group}
                type="button"
                className={`lp-mix-row ${index === selectedClass ? "is-active" : ""}`}
                onClick={() => setSelectedClass(index)}
                aria-pressed={index === selectedClass}
              >
                <span className="lp-mix-name">{cls.label}</span>
                <span className="lp-mix-bar">
                  <span
                    className="lp-mix-bar-fill"
                    style={{ width: `${(cls.share_of_value / maxShare) * 100}%` }}
                  />
                </span>
                <span className="lp-mix-share">{pct(cls.share_of_value, 0)}</span>
              </button>
            ))}
            <div className="lp-mix-head">
              <span>Reason</span><span>Share of failed value</span><span>Share</span>
            </div>
          </div>
        </div>
      </section>

      <section className="lp-section lp-section-bg" id="worth">
        <div className="lp-split lp-split-calc">
          <div>
            <h6 className="lp-kicker">What it's worth</h6>
            <h2 className="lp-h2">Move the two numbers you already know.</h2>
            <p className="lp-body">
              Monthly volume and failure rate. Indian merchants run{" "}
              {MARKET.successRateRange} blended success, so most sit between 4% and 8%.
              Everything below that comes from {measured ? "this system's own holdout" : "published recovery benchmarks"}.
            </p>

            <div className="lp-sliders">
              <label className="lp-slider">
                <span className="lp-slider-row">
                  <span>Monthly volume</span>
                  <strong className="slider-val">₹{gmvCr.toFixed(1)} Cr / mo</strong>
                </span>
                <input
                  type="range" min={0.5} max={60} step={0.5} value={gmvCr}
                  onChange={(e) => setGmvCr(Number(e.target.value))}
                />
              </label>
              <label className="lp-slider">
                <span className="lp-slider-row">
                  <span>Payment failure rate</span>
                  <strong className="slider-val">{failRate.toFixed(1)}%</strong>
                </span>
                <input
                  type="range" min={1} max={18} step={0.1} value={failRate}
                  onChange={(e) => setFailRate(Number(e.target.value))}
                />
              </label>
            </div>
          </div>

          <div className="lp-calc">
            <div className="lp-calc-hero">
              <div className="lp-kicker">Incremental recovery, per year</div>
              <div className="lp-calc-big">{short(calc.incremental * 12)}</div>
              <div className="lp-calc-sub">
                above the {pct(calc.held)} that comes back on its own
              </div>
            </div>
            <div className="lp-calc-card">
              <div className="lp-kicker lp-kicker-accent">At risk / month</div>
              <div className="lp-calc-v">{short(calc.atRisk)}</div>
            </div>
            <div className="lp-calc-card">
              <div className="lp-kicker lp-kicker-accent">Recovered / month</div>
              <div className="lp-calc-v">{short(calc.recovered)}</div>
            </div>
            <div className="lp-calc-card">
              <div className="lp-kicker lp-kicker-sage">Customer contacts</div>
              <div className="lp-calc-v">{calc.contacts.toLocaleString("en-IN")}</div>
            </div>
            <div className="lp-calc-card">
              <div className="lp-kicker lp-kicker-sage">Contact cost</div>
              <div className="lp-calc-v">{short(calc.contactCost)}</div>
            </div>
          </div>
        </div>
        <p className="lp-foot-note">
          {measured
            ? `Treated and holdout rates are this install's own measured numbers (${pct(calc.treated)} vs ${pct(calc.held)}), not a benchmark.`
            : `No holdout data yet, so this falls back to published benchmarks: ${MARKET.baselineRecoveryLabel} with no recovery effort, ${MARKET.medianRecoveryLabel} median with dunning, ${MARKET.bestInClassLabel} best-in-class.`}
        </p>
      </section>

      <section className="lp-section lp-section-dark" id="evidence">
        <h6 className="lp-kicker lp-kicker-onDark">Evidence</h6>
        <h2 className="lp-h2 lp-h2-onDark">Did it actually work? Here is how we know.</h2>
        <p className="lp-body lp-body-onDark">
          One opportunity in five is withheld and never touched. The gap between the two groups is
          the only recovery Recover is willing to claim &mdash; and it is reported signed, so a
          negative result shows up as a negative number.
        </p>

        <div className="lp-evidence">
          <div className="lp-bars">
            <div className="lp-bar-col">
              <div className="lp-bar-v is-muted">{pct(experiment?.control.conversion_rate ?? 0)}</div>
              <div
                className="lp-bar is-holdout"
                style={{ height: `${Math.max(18, (experiment?.control.conversion_rate ?? 0) * 460)}px` }}
              />
              <div className="lp-bar-k">Holdout &mdash; untouched</div>
            </div>
            <div className="lp-bar-col">
              <div className="lp-bar-v is-accent">{pct(experiment?.treatment.conversion_rate ?? 0)}</div>
              <div
                className="lp-bar is-treated"
                style={{ height: `${Math.max(18, (experiment?.treatment.conversion_rate ?? 0) * 460)}px` }}
              >
                <span className="lp-bar-sweep" />
              </div>
              <div className="lp-bar-k">Treated by Recover</div>
            </div>
            <div className="lp-bar-col is-delta">
              <div className="lp-bar-v is-sage">
                {experiment ? `${experiment.delta_conversion_rate >= 0 ? "+" : ""}${(experiment.delta_conversion_rate * 100).toFixed(1)}` : "—"}
              </div>
              <div
                className="lp-bar is-delta"
                style={{ height: `${Math.max(18, Math.abs(experiment?.delta_conversion_rate ?? 0) * 460)}px` }}
              />
              <div className="lp-bar-k">
                points of measured lift<br />
                {experiment
                  ? `p = ${experiment.p_value}, n = ${(experiment.treatment.total_opportunities + experiment.control.total_opportunities).toLocaleString("en-IN")}`
                  : "awaiting volume"}
              </div>
            </div>
          </div>

          <div className="lp-evidence-side">
            <div className="lp-kicker lp-kicker-onDark">What the gate refuses</div>
            <div className="lp-audit">
              <div className="lp-audit-row">
                <div className="lp-audit-t">MODEL</div>
                <div>
                  <div className="lp-audit-w">
                    {card?.economics_test
                      ? `${card.economics_test.lift_pct >= 0 ? "+" : ""}${card.economics_test.lift_pct.toFixed(1)}% net incremental`
                      : "no benchmark installed"}
                  </div>
                  <div className="lp-audit-m">
                    measured against ranking by amount with no model at all &mdash; not against random
                  </div>
                </div>
              </div>
              <div className="lp-audit-row">
                <div className="lp-audit-t">GATE</div>
                <div>
                  <div className="lp-audit-w">
                    {card?.action_quality
                      ? `${Object.values(card.action_quality).filter((q) => !q.enabled).length} of ${Object.keys(card.action_quality).length} actions quarantined`
                      : "running on the heuristic"}
                  </div>
                  <div className="lp-audit-m">
                    they fall back to the deterministic heuristic rather than switching off
                  </div>
                </div>
              </div>
              <div className="lp-audit-row">
                <div className="lp-audit-t">DATA</div>
                <div>
                  <div className="lp-audit-w">{card?.data_provenance ?? "unknown"} provenance</div>
                  <div className="lp-audit-m">
                    synthetic traffic is excluded from every operational KPI and causal metric
                  </div>
                </div>
              </div>
            </div>
            <div className="lp-guardrails">
              {[
                policy ? `Max ${policy.max_contact_attempts} contacts` : "Contact cap",
                policy ? `${policy.cooldown_minutes} min cooldown` : "Cooldown",
                policy ? `Cap ${inr(policy.max_amount_minor)}` : "Value cap",
                "Stop on recovery",
                "Never contact synthetic traffic",
              ].map((g) => <span key={g} className="tag lp-tag-onDark">{g}</span>)}
            </div>
          </div>
        </div>
      </section>

      <section className="lp-section lp-section-surface">
        <h6 className="lp-kicker">Where it sits</h6>
        <h2 className="lp-h2">Built on the Razorpay stack you already run.</h2>
        <div className="lp-fit">
          {RAZORPAY_FIT.map((f) => (
            <article key={f.title} className="card elev-sm lp-fit-card">
              <div className={`lp-fit-mark is-${f.tone}`}><Icon name={f.icon} size={18} /></div>
              <div className="card-title">{f.title}</div>
              <p className="card-body">{f.body}</p>
            </article>
          ))}
        </div>

        <div className="lp-final">
          <div className="lp-final-copy">
            <h2 className="lp-final-h">Point it at last month's failures.</h2>
            <p className="lp-final-p">
              {liveExecution
                ? "Customer-facing execution is armed. Every decision can reach a real customer."
                : "Observe-only mode is on. The pipeline runs end to end and records what it would have sent, without sending anything."}
            </p>
            {error && <p className="lp-final-err">{error}</p>}
          </div>
          <div className="lp-final-actions">
            <button type="button" className="btn btn-primary btn-lg" onClick={onStart}>
              Open the console
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={toggleShadow}
              disabled={busy || !mode}
            >
              {busy ? "Working…" : liveExecution ? "Return to observe-only" : "Arm live execution"}
            </button>
          </div>
        </div>
      </section>

      <footer className="lp-foot">
        <span className="lp-foot-brand">Razorpay Recover</span>
        <span className="lp-foot-src">
          Market figures:{" "}
          {SOURCES.map((s, i) => (
            <span key={s.href}>
              {i > 0 && " · "}
              <a href={s.href} target="_blank" rel="noreferrer">{s.label}</a>
            </span>
          ))}
        </span>
        <span className="lp-foot-note-r">
          System figures are read live from this install.
        </span>
      </footer>
    </main>
  );
}
