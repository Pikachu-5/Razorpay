import { useEffect, useState } from "react";
import {
  fetchExperimentMetrics,
  fetchMlStatus,
  fetchModelCard,
  fetchModelComparison,
  fetchPolicyConfig,
  promoteModel,
} from "../../api/client";
import type {
  ExperimentMetrics,
  MlStatus,
  ModelCard,
  ModelComparison,
  PolicyConfig,
} from "../../api/types";
import { inr } from "../../utils/format";
import { ConfirmAction } from "../ConfirmAction";
import { Icon } from "../Icon";

export function GovernanceTab() {
  const [mlStatus, setMlStatus] = useState<MlStatus | null>(null);
  const [modelCard, setModelCard] = useState<ModelCard | null>(null);
  const [policyConfig, setPolicyConfig] = useState<PolicyConfig | null>(null);
  const [experiment, setExperiment] = useState<ExperimentMetrics | null>(null);
  const [comparison, setComparison] = useState<ModelComparison | null>(null);
  const [selectedChallengerVer, setSelectedChallengerVer] = useState<string>("");
  const [promoting, setPromoting] = useState(false);
  const [promoteMsg, setPromoteMsg] = useState<string | null>(null);
  const [includeSimulated, setIncludeSimulated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingPromote, setPendingPromote] = useState(false);

  useEffect(() => {
    loadGovernanceData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [includeSimulated]);

  async function loadGovernanceData() {
    setLoading(true);
    setError(null);
    try {
      const [ml, card, pol, exp, comp] = await Promise.all([
        fetchMlStatus(),
        fetchModelCard(),
        fetchPolicyConfig(),
        fetchExperimentMetrics(includeSimulated).catch(() => null),
        fetchModelComparison().catch(() => null),
      ]);
      setMlStatus(ml);
      setModelCard(card);
      setPolicyConfig(pol);
      setExperiment(exp);
      setComparison(comp);
      if (comp?.all_cards && comp.all_cards.length > 0) {
        setSelectedChallengerVer(comp.all_cards[comp.all_cards.length - 1].version);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load governance details");
    } finally {
      setLoading(false);
    }
  }

  async function handlePromote(force: boolean = false) {
    setPendingPromote(false);
    setPromoting(true);
    setPromoteMsg(null);
    try {
      const res = await promoteModel(selectedChallengerVer, force) as { version?: string };
      setPromoteMsg(
        `Promoted recovery_model_${res.version ?? selectedChallengerVer}.pkl. ` +
        `It is now the artifact every decision loads.`,
      );
      await loadGovernanceData();
    } catch (err) {
      setError(`Promotion rejected: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setPromoting(false);
    }
  }


  const challengerCard = comparison?.all_cards?.find(
    (c) => c.version === selectedChallengerVer
  );

  // No promotion pointer means no model drives decisions, whatever artifacts
  // happen to sit in the directory.
  const runningOnHeuristic = mlStatus ? mlStatus.promoted_pointer !== true : false;
  const isActiveModel = !runningOnHeuristic && mlStatus?.model_version === selectedChallengerVer;
  // With nothing promoted the active "card" is the heuristic stub, which has no
  // metrics. Rather than showing empty evidence panels, fall through to the
  // selected candidate and label whose evidence is on screen.
  const evidenceCard = runningOnHeuristic ? challengerCard : modelCard;
  const evidenceIsCandidate = runningOnHeuristic && Boolean(challengerCard);
  const testMetrics = evidenceCard?.per_action?.test ?? {};
  const economics = evidenceCard?.economics_test;
  const candidateLift = challengerCard?.economics_test?.lift_pct;
  const candidateEnabled = Object.entries(challengerCard?.action_quality ?? {})
    .filter(([, quality]) => quality.enabled)
    .map(([action]) => action);

  return (
    <div className="governance-container">
      {pendingPromote && (
        <ConfirmAction
          title="Promote this model"
          summary={
            "The promoted artifact is loaded by every decision from the moment it is written. " +
            "Actions the gate quarantined keep falling back to the heuristic; they are not switched off."
          }
          facts={[
            { label: "Candidate", value: `recovery_model_${selectedChallengerVer}.pkl` },
            {
              label: "Replaces",
              value: runningOnHeuristic
                ? "the deterministic heuristic (no model is promoted)"
                : `recovery_model_${mlStatus?.model_version}.pkl`,
            },
            {
              label: "Actions it will drive",
              value: candidateEnabled.length
                ? candidateEnabled.join(", ")
                : "none — every action failed the quality gate",
              emphasis: candidateEnabled.length === 0,
            },
            {
              label: "Offline economics",
              value: typeof candidateLift === "number"
                ? `${candidateLift > 0 ? "+" : ""}${candidateLift.toFixed(1)}% net incremental vs the no-model baseline`
                : "no benchmark on this card",
              emphasis: typeof candidateLift === "number" && candidateLift < 0,
            },
            { label: "Training data", value: challengerCard?.data_provenance ?? "unknown" },
          ]}
          confirmLabel="Promote model"
          danger
          impactLabel="Changes every decision"
          safeNote={
            "Promotion is reversible: promote the previous version to roll back. " +
            "The gate still runs — a candidate that fails it is rejected, not promoted."
          }
          busy={promoting}
          onConfirm={() => handlePromote(false)}
          onCancel={() => setPendingPromote(false)}
        />
      )}

      {/* Header */}
      <div className="panel panel-header-row">
        <div>
          <h2>Model evidence and experiment</h2>
          <span className="panel-sub">
            Separate offline model quality from live causal evidence.
          </span>
        </div>

        <button
          type="button"
          className="btn btn-secondary"
          onClick={loadGovernanceData}
          disabled={loading}
        >
          <Icon name="refresh" size={15} className={loading ? "is-spinning" : undefined} />{loading ? "Loading" : "Refresh"}
        </button>
      </div>

      {error && <div className="alert-box alert-error">{error}</div>}
      {promoteMsg && <div className="alert-box alert-info">{promoteMsg}</div>}

      {/* Live Counterfactual A/B Experimentation Section */}
      <section className="panel experiment-panel">
        <div className="panel-header">
          <div>
            <h3>Live treatment and holdout experiment</h3>
            <span className="panel-sub">
              Stable 80/20 assignment.{" "}
              {includeSimulated
                ? "Including simulated demo traffic — a demonstration of the measurement machinery, not evidence of real-world lift."
                : "Synthetic opportunities are excluded."}
            </span>
            <label className="sim-toggle">
              <input
                type="checkbox"
                checked={includeSimulated}
                onChange={(e) => setIncludeSimulated(e.target.checked)}
              />
              <span>Include simulated traffic</span>
            </label>
          </div>
          {experiment && (
            <span
              className={`chip ${
                experiment.statistically_significant ? "chip-good" : "chip-neutral"
              }`}
            >
              {experiment.minimum_sample_met || includeSimulated
                ? experiment.statistically_significant ? `Significant · p=${experiment.p_value}` : `Not significant · p=${experiment.p_value}`
                : "Insufficient sample"}
            </span>
          )}
        </div>

        {experiment ? (
          <div className="experiment-grid">
            <div className="exp-card exp-card-highlight">
              <span className="exp-lbl">Incremental revenue estimate</span>
              <div className="exp-val-big">
                {experiment.minimum_sample_met || includeSimulated
                  ? inr(experiment.incremental_revenue_minor)
                  : "Not yet measurable"}
              </div>
              <span className="exp-sub">
                {experiment.minimum_sample_met || includeSimulated
                  ? `${experiment.causal_lift_pct > 0 ? "+" : ""}${experiment.causal_lift_pct}% versus natural recovery${includeSimulated ? " (simulated)" : ""}`
                  : "Requires at least 100 non-synthetic cases in each group"}
              </span>
            </div>

            <div className="exp-card">
              <span className="exp-lbl">Treatment group</span>
              <div className="exp-val good-val">
                {(experiment.treatment.conversion_rate * 100).toFixed(1)}% Conversion
              </div>
              <span className="exp-sub">
                {experiment.treatment.recovered_count} / {experiment.treatment.total_opportunities} recovered ({inr(experiment.treatment.recovered_amount_minor)})
              </span>
            </div>

            <div className="exp-card">
              <span className="exp-lbl">Control holdout</span>
              <div className="exp-val muted-val">
                {(experiment.control.conversion_rate * 100).toFixed(1)}% Natural Recovery
              </div>
              <span className="exp-sub">
                {experiment.control.recovered_count} / {experiment.control.total_opportunities} recovered ({inr(experiment.control.recovered_amount_minor)})
              </span>
            </div>

            <div className="exp-card">
              <span className="exp-lbl">Two-proportion test</span>
              <div className="exp-val">z = {experiment.z_score}</div>
              <span className="exp-sub">
                Two-tailed p-value: {experiment.p_value} ({experiment.p_value < 0.05 ? "95%+ Confidence" : "Awaiting Volume"})
              </span>
            </div>
          </div>
        ) : (
          <p className="empty">Loading A/B experimentation metrics…</p>
        )}
      </section>

      {/* Automated Model Promotion Gate Section */}
      <section className="panel promotion-panel">
        <div className="panel-header">
          <div>
            <h3>Model promotion gate</h3>
            <span className="panel-sub">
              Candidate vs Champion comparator across AUC, calibration gap, and economics lift
            </span>
          </div>

          <div className="promote-controls">
            <label className="select-label">Select Candidate:</label>
            <select
              className="select-filter"
              value={selectedChallengerVer}
              onChange={(e) => setSelectedChallengerVer(e.target.value)}
            >
              {(comparison?.all_cards || []).map((c) => (
                <option key={c.version} value={c.version}>
                  Model {c.version} ({c.model_type.toUpperCase()})
                </option>
              ))}
            </select>

            <button
              type="button"
              className="btn btn-primary"
              onClick={() => setPendingPromote(true)}
              disabled={!challengerCard || promoting || isActiveModel}
            >
              {isActiveModel
                ? "Active promoted model"
                : promoting
                ? "Promoting…"
                : "Promote, observe only"}
            </button>
          </div>
        </div>

        {challengerCard && (
          <div className="comparator-grid">
            <div className="comp-card">
              <span className="comp-title">Candidate details</span>
              <div className="comp-row">
                <span>Version:</span>
                <code>{challengerCard.version}</code>
              </div>
              <div className="comp-row">
                <span>Model Architecture:</span>
                <span>{challengerCard.model_type.toUpperCase()}</span>
              </div>
              <div className="comp-row">
                <span>Offline synthetic lift:</span>
                <strong className={(challengerCard.economics_test?.lift_pct ?? 0) >= 0 ? "good-val" : "lift-negative"}>
                  {(challengerCard.economics_test?.lift_pct ?? 0) > 0 ? "+" : ""}{challengerCard.economics_test?.lift_pct?.toFixed(1) ?? "0"}%
                </strong>
              </div>
            </div>

            <div className="comp-card">
              <span className="comp-title">Primary action test performance</span>
              {challengerCard.per_action?.test?.send_payment_link ? (
                <div className="comp-row">
                  <span>Payment Link AUC:</span>
                  <strong>
                    {challengerCard.per_action.test.send_payment_link.roc_auc.toFixed(3)}
                  </strong>
                </div>
              ) : (
                <div className="comp-row"><span>AUC:</span><span>N/A</span></div>
              )}
              {challengerCard.per_action?.test?.send_payment_link && (
                <div className="comp-row">
                  <span>Calibration Gap:</span>
                  <span className={`chip ${Math.abs(
                    challengerCard.per_action.test.send_payment_link.mean_predicted -
                      challengerCard.per_action.test.send_payment_link.positive_rate
                  ) <= 0.12 ? "chip-good" : "chip-warn"}`}>
                    {(
                      Math.abs(
                        challengerCard.per_action.test.send_payment_link.mean_predicted -
                          challengerCard.per_action.test.send_payment_link.positive_rate
                      ) * 100
                    ).toFixed(1)}% {Math.abs(
                      challengerCard.per_action.test.send_payment_link.mean_predicted -
                        challengerCard.per_action.test.send_payment_link.positive_rate
                    ) <= 0.12 ? "(Well Calibrated)" : "(Needs Recalibration)"}
                  </span>
                </div>
              )}
            </div>
          </div>
        )}
      </section>

      {/* Model Overview & Economics Lift Grid */}
      <div className="gov-grid">
        {/* ML Model Card Summary */}
        <section className="panel">
          <div className="panel-header">
            <h3>Active model</h3>
            <span className={`chip ${runningOnHeuristic ? "chip-neutral" : "chip-good"}`}>
              {runningOnHeuristic ? "Heuristic fallback" : "Guarded artifact"}
            </span>
          </div>

          {runningOnHeuristic && (
            <p className="econ-expl">
              No artifact has passed the promotion gate, so every decision is made by the
              deterministic heuristic. Artifacts in the registry are inert until promoted &mdash;
              dropping a <code>.pkl</code> into the directory does not put it on live traffic.
            </p>
          )}

          <div className="meta-list">
            <div className="meta-row">
              <span className="meta-k">Model Version:</span>
              <span className="meta-v"><code>{mlStatus?.model_version ?? "Unavailable"}</code></span>
            </div>
            <div className="meta-row">
              <span className="meta-k">Model Architecture:</span>
              <span className="meta-v">
                {modelCard?.model_type?.toUpperCase() ?? "Unavailable"}
              </span>
            </div>
            <div className="meta-row">
              <span className="meta-k">Trained Timestamp:</span>
              <span className="meta-v">
                {modelCard?.trained_at
                  ? new Date(modelCard.trained_at).toLocaleString("en-IN")
                  : "Not available"}
              </span>
            </div>
            <div className="meta-row">
              <span className="meta-k">Dataset Splits (Train / Val / Test):</span>
              <span className="meta-v mono">
                {modelCard?.rows_train_val_test
                  ? `${modelCard.rows_train_val_test[0].toLocaleString()} / ${modelCard.rows_train_val_test[1].toLocaleString()} / ${modelCard.rows_train_val_test[2].toLocaleString()}`
                  : "Not available"}
              </span>
            </div>
            <div className="meta-row">
              <span className="meta-k">Enabled model actions:</span>
              <span className="meta-v">
                {mlStatus?.actions_enabled?.length
                  ? mlStatus.actions_enabled.map((a) => (
                      <code key={a} className="action-pill-sm">{a}</code>
                    ))
                  : "No action model passed its gate"}
              </span>
            </div>
            {mlStatus?.actions_heuristic_fallback?.length ? (
              <div className="meta-row">
                <span className="meta-k">Quarantined, served by heuristic:</span>
                <span className="meta-v">
                  {mlStatus.actions_heuristic_fallback.map((a) => (
                    <code key={a} className="action-pill-sm">{a}</code>
                  ))}
                </span>
              </div>
            ) : null}
            <div className="meta-row">
              <span className="meta-k">Natural recovery baseline:</span>
              <span className="meta-v">
                {mlStatus?.natural_recovery_baseline
                  ? Object.entries(mlStatus.natural_recovery_baseline)
                      .filter(([group]) => group !== "unknown")
                      .map(([group, rate]) => `${group} ${(rate * 100).toFixed(0)}%`)
                      .join(" · ")
                  : "unavailable"}
              </span>
            </div>
            <div className="meta-row">
              <span className="meta-k">Training provenance:</span>
              <span className="meta-v">{mlStatus?.data_provenance ?? "unknown"}</span>
            </div>
          </div>
        </section>

        {/* Economics Simulation Lift Card */}
        <section className="panel economics-card">
          <div className="panel-header">
            <h3>Offline economics benchmark</h3>
            <span className="chip chip-purple">
              {evidenceIsCandidate
                ? `Candidate ${evidenceCard?.version ?? ""} · not promoted`
                : "Synthetic · demo only"}
            </span>
          </div>

          {economics?.arms ? (
            <>
              <div className="lift-highlight-box">
                <div className={`lift-number ${economics.lift_pct >= 0 ? "" : "lift-negative"}`}>
                  {economics.lift_pct > 0 ? "+" : ""}{economics.lift_pct.toFixed(1)}%
                </div>
                <div className="lift-label">
                  Net incremental recovery versus the best policy that uses no model
                </div>
              </div>

              <div className="economics-comparison">
                <div className="econ-stat">
                  <span className="econ-label">Model + policy</span>
                  <span className="econ-val good-val">
                    {inr(economics.arms.model_policy.net_incremental_minor)}
                  </span>
                </div>
                <div className="econ-stat">
                  <span className="econ-label">Rank by amount, no model</span>
                  <span className="econ-val muted-val">
                    {inr(economics.arms.value_ranked_link.net_incremental_minor)}
                  </span>
                </div>
                <div className="econ-stat">
                  <span className="econ-label">Random selection</span>
                  <span className="econ-val muted-val">
                    {inr(economics.arms.random_link.net_incremental_minor)}
                  </span>
                </div>
              </div>

              <p className="econ-expl">
                All three arms intervene on the same number of opportunities
                ({economics.budget_k ?? 0} of {economics.universe ?? economics.opportunities_scored})
                and are scored on realised outcomes, minus the recovery expected without any
                intervention, minus what the actions cost.
              </p>
            </>
          ) : (
            <p className="empty">No held-out economics benchmark is installed for the active model.</p>
          )}

          <p className="econ-expl">
            Gross recovered revenue is deliberately not the headline: a policy that targets
            whatever was going to recover anyway scores well on it and creates nothing. This is
            an offline replay on synthetic rows, not a claim about live revenue &mdash; the
            holdout experiment above is the production evidence path.
          </p>
          {economics?.caveats?.length ? (
            <ul className="econ-caveats">
              {economics.caveats.map((caveat) => <li key={caveat}>{caveat}</li>)}
            </ul>
          ) : null}
        </section>
      </div>

      {/* Model Performance & Calibration Metrics Table */}
      <section className="panel">
        <div className="panel-header">
          <div>
            <h3>
              Per-action quality gates
              {evidenceIsCandidate && ` — candidate ${evidenceCard?.version ?? ""}`}
            </h3>
            <span className="panel-sub">
              Evaluated on out-of-time test set (no future leakage)
            </span>
          </div>
        </div>

        <div className="table-responsive">
          <table className="table">
            <thead>
              <tr>
                <th>Candidate Action</th>
                <th>Test Samples (n)</th>
                <th>Actual Positive Rate</th>
                <th>Mean Predicted Prob</th>
                <th>Calibration Gap</th>
                <th>ROC-AUC</th>
                <th>Brier Score</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(testMetrics).map(([action, m]) => {
                const gap = Math.abs(m.mean_predicted - m.positive_rate);
                const isCalibrated = gap <= 0.12;
                // Read the gate result off the card whose metrics this row
                // shows. Reading it off the live model instead marked every
                // action quarantined whenever nothing was promoted.
                const passedGate = evidenceCard?.action_quality?.[action]?.enabled === true;

                return (
                  <tr key={action}>
                    <td><code>{action}</code></td>
                    <td className="num">{m.n}</td>
                    <td>{(m.positive_rate * 100).toFixed(1)}%</td>
                    <td>{(m.mean_predicted * 100).toFixed(1)}%</td>
                    <td>
                      <span className={`calibration-tag ${isCalibrated ? "cal-good" : "cal-warn"}`}>
                        {(gap * 100).toFixed(1)}% {isCalibrated ? "(Well Calibrated)" : "(Needs Recalibration)"}
                      </span>
                    </td>
                    <td className="num font-bold">{m.roc_auc.toFixed(3)}</td>
                    <td className="num">{m.brier.toFixed(3)}</td>
                    <td>
                      <span className={`status-pill ${passedGate ? "st-executed" : "st-failed"}`}>
                        {passedGate
                          ? evidenceIsCandidate ? "PASSED GATE" : "ENABLED"
                          : "QUARANTINED"}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* Deterministic Policy Guardrails Configuration */}
      <section className="panel">
        <div className="panel-header">
          <div>
            <h3>Execution guardrails</h3>
            <span className="panel-sub">
              Hard constraints enforced on every decision before API execution
            </span>
          </div>
        </div>

        <div className="guardrails-grid">
          <div className="guardrail-card">
            <div className="gr-marker" aria-hidden="true" />
            <div className="gr-body">
              <span className="gr-name">Emergency Kill Switch</span>
              <span className="gr-val">
                {policyConfig?.kill_switch ? (
                  <span className="status-pill st-failed">ENGAGED (BLOCKING ALL)</span>
                ) : (
                  <span className="status-pill st-captured">DISENGAGED (ACTIVE)</span>
                )}
              </span>
              <span className="gr-desc">Global kill switch to immediately stop automated recovery.</span>
            </div>
          </div>

          <div className="guardrail-card">
            <div className="gr-marker" aria-hidden="true" />
            <div className="gr-body">
              <span className="gr-name">Maximum Amount Cap</span>
              <span className="gr-val">
                {policyConfig ? `₹${(policyConfig.max_amount_minor / 100).toLocaleString("en-IN")}` : "₹25,000"}
              </span>
              <span className="gr-desc">
                High-value transactions exceeding cap require human review.
              </span>
            </div>
          </div>

          <div className="guardrail-card">
            <div className="gr-marker" aria-hidden="true" />
            <div className="gr-body">
              <span className="gr-name">Max Contact Attempts</span>
              <span className="gr-val">
                {policyConfig?.max_contact_attempts ?? 3} attempts / opportunity
              </span>
              <span className="gr-desc">
                Strict cap to prevent customer spam and notification fatigue.
              </span>
            </div>
          </div>

          <div className="guardrail-card">
            <div className="gr-marker" aria-hidden="true" />
            <div className="gr-body">
              <span className="gr-name">Contact Cooldown</span>
              <span className="gr-val">{policyConfig?.cooldown_minutes ?? 60} minutes</span>
              <span className="gr-desc">
                Minimum wait time between sequential interventions for the same customer.
              </span>
            </div>
          </div>

          <div className="guardrail-card">
            <div className="gr-marker" aria-hidden="true" />
            <div className="gr-body">
              <span className="gr-name">Confidence Floor</span>
              <span className="gr-val">
                {policyConfig ? (policyConfig.confidence_floor * 100).toFixed(0) : "35"}% probability
              </span>
              <span className="gr-desc">
                Minimum predicted model recovery probability to justify active outreach.
              </span>
            </div>
          </div>

          <div className="guardrail-card">
            <div className="gr-marker" aria-hidden="true" />
            <div className="gr-body">
              <span className="gr-name">Minimum EV Margin</span>
              <span className="gr-val">
                {policyConfig ? `₹${(policyConfig.min_ev_margin_minor / 100).toFixed(2)}` : "₹5.00"}
              </span>
              <span className="gr-desc">
                Ensures expected revenue recovery strictly exceeds intervention cost + margin.
              </span>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
