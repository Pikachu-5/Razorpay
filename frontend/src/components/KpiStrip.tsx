import { useEffect, useRef, useState } from "react";
import type { Summary } from "../api/types";
import { inr } from "../utils/format";

const COUNT_MS = 1200;

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true
  );
}

/**
 * Counts a figure up from zero on mount and whenever `resetKey` changes
 * (i.e. an explicit refresh) — never on an incidental re-render, so the
 * numbers stay still while an operator is reading them.
 */
function useCountUp(target: number | null, resetKey: number): number | null {
  const [display, setDisplay] = useState<number | null>(target);
  const frame = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (target == null) {
      setDisplay(null);
      return;
    }
    // requestAnimationFrame is suspended while the tab is hidden or otherwise
    // not compositing, so an animated count would never land. Show the real
    // figure straight away instead of leaving the operator staring at a dash.
    if (prefersReducedMotion() || document.visibilityState === "hidden") {
      setDisplay(target);
      return;
    }
    setDisplay(0);
    const start = performance.now();
    const tick = (now: number) => {
      const p = Math.min(1, (now - start) / COUNT_MS);
      // ease-out cubic
      setDisplay(Math.round(target * (1 - Math.pow(1 - p, 3))));
      if (p < 1) frame.current = requestAnimationFrame(tick);
    };
    frame.current = requestAnimationFrame(tick);
    return () => {
      if (frame.current !== undefined) cancelAnimationFrame(frame.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, resetKey]);

  // Fall back to the true figure for any frame before the animation starts,
  // so the strip is never blank while real data is in hand.
  return display ?? target;
}

export function KpiStrip({
  summary, refreshKey = 0, includeSynthetic = false, onToggleSynthetic,
}: {
  summary: Summary | null;
  refreshKey?: number;
  includeSynthetic?: boolean;
  onToggleSynthetic?: (next: boolean) => void;
}) {
  const recovered = useCountUp(summary ? summary.net_recovered_today_minor : null, refreshKey);
  const atRisk = useCountUp(summary ? summary.revenue_at_risk_minor : null, refreshKey);

  const successRate =
    summary?.success_rate_today == null ? "—" : `${(summary.success_rate_today * 100).toFixed(1)}%`;

  // A demo install has only seeded traffic, so the real-only view is all
  // zeroes. Rather than let that read as "nothing is happening", offer the
  // switch and say plainly which population is on screen.
  const hiddenSynthetic = summary?.synthetic_payments_excluded ?? 0;
  const canToggle = Boolean(onToggleSynthetic) && (includeSynthetic || hiddenSynthetic > 0);

  return (
    <section className="kpi-band">
      <div className="kpi-band-head">
        <span className={`chip ${includeSynthetic ? "chip-amber" : "chip-neutral"}`}>
          {includeSynthetic ? "Including demo traffic" : "Real payments only"}
        </span>
        {canToggle && (
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => onToggleSynthetic?.(!includeSynthetic)}
          >
            {includeSynthetic
              ? "Show real payments only"
              : `Include demo traffic (${hiddenSynthetic.toLocaleString("en-IN")} payments)`}
          </button>
        )}
      </div>

      <dl className="kpi-cards">
        <div className="kpi-card is-hero">
          <dt className="kpi-k">Revenue at risk</dt>
          <dd>
            <span className="kpi-value">{atRisk == null ? "—" : inr(atRisk)}</span>
            <span className="kpi-note">{summary?.open_opportunities ?? 0} unresolved cases</span>
          </dd>
        </div>

        <div className="kpi-card">
          <dt className="kpi-k">Net recovered today</dt>
          <dd>
            <span className="kpi-value">{recovered == null ? "—" : inr(recovered)}</span>
            <span className="kpi-note">
              {summary ? `${inr(summary.revenue_adjustments_today_minor)} refunds / disputes` : "After adjustments"}
            </span>
          </dd>
        </div>

        <div className="kpi-card">
          <dt className="kpi-k">Payment success</dt>
          <dd>
            <span className="kpi-value">{successRate}</span>
            <span className="kpi-note">
              {includeSynthetic ? "all attempts today" : "real attempts today"}
            </span>
          </dd>
        </div>

        <div className="kpi-card is-sage">
          {/* Named for what it counts. A seeded demo has payments but no
              webhook deliveries, and "verified events: 0" next to 168 payments
              reads as a fault rather than as an accurate statement. */}
          <dt className="kpi-k">Signed webhooks</dt>
          <dd>
            <span className="kpi-value">{summary ? summary.events_received_today : "—"}</span>
            <span className="kpi-note">verified and deduplicated today</span>
          </dd>
        </div>
      </dl>
    </section>
  );
}
