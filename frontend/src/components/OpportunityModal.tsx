import { useEffect, useRef, useState } from "react";
import { fetchOperatingMode, fetchOpportunityDetail, redecideOpportunity } from "../api/client";
import type { DecisionAudit, OperatingMode, OpportunityDetail } from "../api/types";
import { inr } from "../utils/format";
import { ConfirmAction } from "./ConfirmAction";
import { Icon } from "./Icon";

interface OpportunityModalProps {
  opportunityId: string | null;
  onClose: () => void;
  onDecided?: () => void;
}

export function OpportunityModal({
  opportunityId,
  onClose,
  onDecided,
}: OpportunityModalProps) {
  const [detail, setDetail] = useState<OpportunityDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [deciding, setDeciding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeAuditIndex, setActiveAuditIndex] = useState(0);
  const [showRaw, setShowRaw] = useState(false);
  const [pendingReDecide, setPendingReDecide] = useState(false);
  const [mode, setMode] = useState<OperatingMode | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!opportunityId) return;
    loadDetail(opportunityId);
    fetchOperatingMode().then(setMode).catch(() => setMode(null));
  }, [opportunityId]);

  useEffect(() => {
    if (!opportunityId) return;
    closeButtonRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !pendingReDecide) onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [opportunityId, onClose, pendingReDecide]);

  async function loadDetail(id: string) {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchOpportunityDetail(id);
      setDetail(res);
      if (res.decisions && res.decisions.length > 0) {
        setActiveAuditIndex(res.decisions.length - 1);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load opportunity detail");
    } finally {
      setLoading(false);
    }
  }

  async function handleReDecide() {
    if (!opportunityId) return;
    setPendingReDecide(false);
    setDeciding(true);
    try {
      await redecideOpportunity(opportunityId);
      await loadDetail(opportunityId);
      onDecided?.();
    } catch (err) {
      setError(`Re-evaluation failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setDeciding(false);
    }
  }

  if (!opportunityId) return null;

  const currentAudit: DecisionAudit | undefined =
    detail?.decisions?.[activeAuditIndex];

  const liveExecution = mode?.customer_side_effects_enabled === true;

  return (
    <div className="modal-overlay" role="presentation" onMouseDown={onClose}>
      {pendingReDecide && (
        <ConfirmAction
          title="Re-run the decision"
          summary={
            "Diagnosis, prediction and the policy engine run again for this opportunity. " +
            "If policy approves an action it executes straight away."
          }
          facts={[
            {
              label: "Opportunity",
              value: detail ? `${inr(detail.opportunity.amount_minor)} · ${detail.opportunity.status}` : opportunityId,
            },
            {
              label: "Contact budget",
              value: detail
                ? `${detail.opportunity.contact_attempts} attempt(s) already used`
                : "unknown",
            },
            {
              label: "Execution mode",
              value: liveExecution
                ? `LIVE — ${mode?.razorpay_mode ?? "test"} mode, this can contact the customer`
                : "Shadow — the decision is recorded, no customer is contacted",
              emphasis: true,
            },
          ]}
          confirmLabel={liveExecution ? "Re-decide and contact" : "Re-decide in shadow"}
          danger={liveExecution}
          safeNote={
            liveExecution
              ? null
              : "Shadow mode is on. This adds a decision to the audit trail and sends nothing."
          }
          busy={deciding}
          onConfirm={handleReDecide}
          onCancel={() => setPendingReDecide(false)}
        />
      )}
      <section className="modal-content" role="dialog" aria-modal="true" aria-labelledby="opportunity-audit-title" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <div className="modal-subtitle">
              OPPORTUNITY DECISION AUDIT TRACE
            </div>
            <h2 id="opportunity-audit-title" className="modal-title">
              {detail ? inr(detail.opportunity.amount_minor) : "Loading…"}{" "}
              <span className={`status-pill st-${detail?.opportunity.status}`}>
                {detail?.opportunity.status}
              </span>
            </h2>
            <span className="mono-id">ID: {opportunityId}</span>
          </div>
          <div className="modal-header-actions">
            {detail?.opportunity.status === "open" && (
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => setPendingReDecide(true)}
                disabled={deciding}
              >
                <Icon name="refresh" size={15} className={deciding ? "is-spinning" : undefined} />{deciding ? "Evaluating" : "Run re-evaluation"}
              </button>
            )}
            <button ref={closeButtonRef} type="button" className="modal-close" onClick={onClose} aria-label="Close opportunity audit">
              <Icon name="x" size={17} />
            </button>
          </div>
        </div>

        <div className="modal-body">
          {loading && <div className="loading-spinner">Loading decision trace…</div>}
          {error && <div className="alert-box alert-error">{error}</div>}

          {detail && (
            <>
              {/* Top Context Cards */}
              <div className="context-cards">
                <div className="ctx-card">
                  <span className="ctx-label">PAYMENT CONTEXT</span>
                  <div className="ctx-val mono">{detail.payment?.razorpay_payment_id ?? "—"}</div>
                  <div className="ctx-sub">
                    {detail.payment?.method?.toUpperCase() ?? "N/A"}
                    {detail.payment?.bank ? ` · ${detail.payment.bank}` : ""}
                  </div>
                  <div className="ctx-reason">
                    Reason: <code>{detail.payment?.error_reason ?? "unknown"}</code>
                  </div>
                  {detail.payment?.error_description && (
                    <div className="ctx-desc">{detail.payment.error_description}</div>
                  )}
                </div>

                <div className="ctx-card">
                  <span className="ctx-label">CUSTOMER PROFILE</span>
                  <div className="ctx-val">{detail.customer?.email ?? "Anonymous Customer"}</div>
                  <div className="ctx-sub mono">{detail.customer?.identity_key ?? "No identity key"}</div>
                  <div className="ctx-meta">
                    Group: <span className="mono">{detail.opportunity.experiment_group}</span> · Contact Attempts:{" "}
                    <strong>{detail.opportunity.contact_attempts} / 3</strong>
                  </div>
                </div>

                <div className="ctx-card">
                  <span className="ctx-label">WINDOW & ECONOMICS</span>
                  <div className="ctx-val">
                    Expected: {inr(detail.opportunity.expected_recovery_minor ?? 0)}
                  </div>
                  <div className="ctx-sub">
                    Best Action: <code>{detail.opportunity.best_action ?? "do_nothing"}</code>
                  </div>
                  <div className="ctx-meta">
                    Window Ends: {new Date(detail.opportunity.window_ends_at).toLocaleString("en-IN")}
                  </div>
                  {detail.opportunity.closed_reason && (
                    <div className="ctx-closed">Reason: {detail.opportunity.closed_reason}</div>
                  )}
                </div>
              </div>

              {/* Multi-Agent Audit Steps */}
              {detail.decisions.length > 0 ? (
                <div className="audit-section">
                  <div className="audit-nav">
                    <span className="section-title">Multi-Agent Decision Chain</span>
                    {detail.decisions.length > 1 && (
                      <div className="audit-tabs">
                        {detail.decisions.map((d, idx) => (
                          <button
                            key={d.id}
                            type="button"
                            className={`audit-tab-btn ${activeAuditIndex === idx ? "audit-tab-active" : ""}`}
                            onClick={() => setActiveAuditIndex(idx)}
                          >
                            Decision #{idx + 1} ({d.trigger})
                          </button>
                        ))}
                      </div>
                    )}
                  </div>

                  {currentAudit && (
                    <div className="audit-chain">
                      {/* Step 1: Diagnosis Agent */}
                      <div className="step-card">
                        <div className="step-header">
                          <span className="step-num">1</span>
                          <div className="step-title-group">
                            <h4>Diagnosis Agent</h4>
                            <span className="step-sub">Observable failure evidence & root cause</span>
                          </div>
                          {currentAudit.diagnosis && (
                            <span className={`chip chip-${currentAudit.diagnosis.classification}`}>
                              {currentAudit.diagnosis.classification.replace("_", " ").toUpperCase()}
                            </span>
                          )}
                        </div>
                        <div className="step-body">
                          {currentAudit.diagnosis ? (
                            <>
                              <p className="diagnosis-summary">{currentAudit.diagnosis.summary}</p>
                              <div className="confidence-meter">
                                <span>Confidence:</span>
                                <div className="meter-track">
                                  <div
                                    className="meter-fill"
                                    style={{
                                      width: `${(currentAudit.diagnosis.confidence || 0) * 100}%`,
                                    }}
                                  />
                                </div>
                                <span className="meter-num">
                                  {Math.round((currentAudit.diagnosis.confidence || 0) * 100)}%
                                </span>
                              </div>
                              {currentAudit.diagnosis.evidence && currentAudit.diagnosis.evidence.length > 0 && (
                                <div className="evidence-tags">
                                  <span className="evidence-title">Observable Facts:</span>
                                  {currentAudit.diagnosis.evidence.map((ev, i) => (
                                    <span key={i} className="evidence-tag">
                                      <Icon name="check" size={13} /> {ev}
                                    </span>
                                  ))}
                                </div>
                              )}
                            </>
                          ) : (
                            <p className="empty">No diagnosis logged for this decision.</p>
                          )}
                        </div>
                      </div>

                      {/* Step 2: Revenue ML Agent */}
                      <div className="step-card">
                        <div className="step-header">
                          <span className="step-num">2</span>
                          <div className="step-title-group">
                            <h4>Revenue Agent & Recovery Model</h4>
                            <span className="step-sub">
                              Model: <code>{currentAudit.model_version ?? "v2"}</code> · EV = (p − natural recovery) × amount − cost
                            </span>
                          </div>
                          {currentAudit.recommended_action && (
                            <span className="chip chip-purple">
                              Rank 1: {currentAudit.recommended_action}
                            </span>
                          )}
                        </div>
                        <div className="step-body">
                          {currentAudit.predictions ? (
                            <table className="table mini-table">
                              <thead>
                                <tr>
                                  <th>Candidate Action</th>
                                  <th>Recovery prob (p)</th>
                                  <th>Intervention Cost</th>
                                  <th>Expected Value (EV)</th>
                                  <th>Ranking</th>
                                </tr>
                              </thead>
                              <tbody>
                                {Object.entries(currentAudit.predictions)
                                  .sort((a, b) => (b[1].expected_recovery_minor || 0) - (a[1].expected_recovery_minor || 0))
                                  .map(([action, p], i) => (
                                    <tr
                                      key={action}
                                      className={action === currentAudit.recommended_action ? "row-highlight" : ""}
                                    >
                                      <td>
                                        <code className="action-code">{action}</code>
                                        {action === currentAudit.recommended_action && (
                                          <span className="badge-best">RECOMMENDED</span>
                                        )}
                                      </td>
                                      <td>
                                        {p.probability !== null && p.probability !== undefined ? (
                                          <div className="prob-cell">
                                            <div className="prob-bar">
                                              <div
                                                className="prob-fill"
                                                style={{ width: `${p.probability * 100}%` }}
                                              />
                                            </div>
                                            <span>{(p.probability * 100).toFixed(1)}%</span>
                                          </div>
                                        ) : (
                                          "—"
                                        )}
                                      </td>
                                      <td>{inr(p.cost_minor)}</td>
                                      <td className="ev-val">{inr(p.expected_recovery_minor)}</td>
                                      <td className="num">#{i + 1}</td>
                                    </tr>
                                  ))}
                              </tbody>
                            </table>
                          ) : (
                            <p className="empty">No action predictions recorded.</p>
                          )}
                        </div>
                      </div>

                      {/* Step 3: Policy Guardrail Engine */}
                      <div className="step-card">
                        <div className="step-header">
                          <span className="step-num">3</span>
                          <div className="step-title-group">
                            <h4>Policy Engine Guardrails</h4>
                            <span className="step-sub">
                              Deterministic safety & economics rules evaluated before execution
                            </span>
                          </div>
                          {currentAudit.policy_decision && (
                            <span
                              className={`chip ${
                                currentAudit.policy_decision.allowed ? "chip-good" : "chip-risk"
                              }`}
                            >
                              {currentAudit.policy_decision.allowed ? "PASSED ALL RULES" : "BLOCKED BY POLICY"}
                            </span>
                          )}
                        </div>
                        <div className="step-body">
                          {currentAudit.policy_decision?.rules ? (
                            <div className="rules-grid">
                              {currentAudit.policy_decision.rules.map((r, i) => (
                                <div
                                  key={i}
                                  className={`rule-item ${r.passed ? "rule-passed" : "rule-failed"}`}
                                >
                                  <span className="rule-icon"><Icon name={r.passed ? "check" : "x"} size={14} /></span>
                                  <div className="rule-info">
                                    <span className="rule-name">{r.rule.replace(/_/g, " ").toUpperCase()}</span>
                                    <span className="rule-detail">{r.detail}</span>
                                  </div>
                                </div>
                              ))}
                            </div>
                          ) : (
                            <p className="empty">No policy rules evaluated.</p>
                          )}
                        </div>
                      </div>

                      {/* Step 4: Execution Result */}
                      <div className="step-card">
                        <div className="step-header">
                          <span className="step-num">4</span>
                          <div className="step-title-group">
                            <h4>Execution & Real Interventions</h4>
                            <span className="step-sub">
                              Action: <code>{currentAudit.executed_action}</code> · Status:{" "}
                              <strong>{String(currentAudit.execution_result?.status ?? "—")}</strong>
                            </span>
                          </div>
                          <span
                            className={`chip ${
                              currentAudit.execution_result?.status === "executed"
                                ? "chip-good"
                                : currentAudit.execution_result?.status === "closed"
                                ? "chip-neutral"
                                : "chip-warn"
                            }`}
                          >
                            {String(currentAudit.execution_result?.status ?? "N/A").toUpperCase()}
                          </span>
                        </div>
                        <div className="step-body">
                          {currentAudit.execution_result ? (
                            <div className="exec-details">
                              {Boolean(currentAudit.execution_result.razorpay_payment_link_id) && (
                                <div className="exec-row highlight-box">
                                  <span className="exec-k">Razorpay Payment Link:</span>
                                  <span className="exec-v mono">
                                    {String(currentAudit.execution_result.razorpay_payment_link_id)}
                                  </span>
                                  {Boolean(currentAudit.execution_result.short_url) && (
                                    <a
                                      href={String(currentAudit.execution_result.short_url)}
                                      target="_blank"
                                      rel="noreferrer"
                                      className="btn-link"
                                    >
                                      Open link <Icon name="arrow-right" size={14} />
                                    </a>
                                  )}
                                </div>
                              )}
                              {Boolean(currentAudit.execution_result.note) && (
                                <div className="exec-row">
                                  <span className="exec-k">Decision Note:</span>
                                  <span className="exec-v">{String(currentAudit.execution_result.note)}</span>
                                </div>
                              )}
                              {Boolean(currentAudit.execution_result.simulated) && (
                                <div className="exec-row">
                                  <span className="exec-k">Execution Mode:</span>
                                  <span className="exec-v chip chip-warn">Internal Simulation / Logged</span>
                                </div>
                              )}
                            </div>
                          ) : (
                            <p className="empty">No execution result logged.</p>
                          )}
                        </div>
                      </div>

                      {/* Step 5: Verification & Attribution */}
                      <div className="step-card">
                        <div className="step-header">
                          <span className="step-num">5</span>
                          <div className="step-title-group">
                            <h4>Verification & Revenue Attribution</h4>
                            <span className="step-sub">
                              Outcome attribution (Natural Recovery vs Intervention Recovery)
                            </span>
                          </div>
                          <span className="chip chip-info">
                            {currentAudit.verified_outcome?.toUpperCase() ?? "PENDING"}
                          </span>
                        </div>
                        <div className="step-body">
                          <div className="attrib-box">
                            <div className="attrib-stat">
                              <span className="attrib-label">Verified Outcome</span>
                              <span className="attrib-val">
                                {currentAudit.verified_outcome ?? "Awaiting Payment"}
                              </span>
                            </div>
                            <div className="attrib-stat">
                              <span className="attrib-label">Recovered Amount</span>
                              <span className="attrib-val">
                                {inr(currentAudit.recovered_amount_minor ?? 0)}
                              </span>
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <div className="alert-box alert-info">
                  No automated decisions have run for this opportunity yet. Select <strong>Run re-evaluation</strong> above to evaluate it now.
                </div>
              )}

              {/* Interventions Log */}
              {detail.interventions && detail.interventions.length > 0 && (
                <div className="interventions-section">
                  <h3>Executed Interventions History</h3>
                  <table className="table mini-table">
                    <thead>
                      <tr>
                        <th>Action</th>
                        <th>Status</th>
                        <th>Reference</th>
                        <th>Timestamp</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.interventions.map((iv, idx) => (
                        <tr key={idx}>
                          <td><code>{iv.action}</code></td>
                          <td><span className="status-pill st-executed">{iv.status}</span></td>
                          <td className="mono">{iv.reference ?? "internal"}</td>
                          <td className="time-cell">{new Date(iv.created_at).toLocaleString("en-IN")}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* Raw JSON Debug View */}
              <div className="raw-toggle-section">
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => setShowRaw(!showRaw)}
                >
                  {showRaw ? "Hide Raw JSON" : "Inspect Raw Opportunity & Decision JSON"}
                </button>
                {showRaw && (
                  <pre className="raw-json-viewer">
                    {JSON.stringify(detail, null, 2)}
                  </pre>
                )}
              </div>
            </>
          )}
        </div>
      </section>
    </div>
  );
}
