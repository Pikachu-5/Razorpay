import { useEffect, useState } from "react";
import {
  fetchIncidents,
  fetchIncidentDetail,
  fetchOperatingMode,
  triggerIncidentResponse,
  triggerIncidentScan,
} from "../../api/client";
import type { Incident, OperatingMode } from "../../api/types";
import { inr } from "../../utils/format";
import { ConfirmAction } from "../ConfirmAction";
import { Icon } from "../Icon";

interface IncidentsTabProps {
  onSelectOpportunity?: (id: string) => void;
  refreshSignal?: number;
}

export function IncidentsTab({ onSelectOpportunity, refreshSignal }: IncidentsTabProps) {
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [loading, setLoading] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [respondingId, setRespondingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scanResult, setScanResult] = useState<string | null>(null);
  const [responseResult, setResponseResult] = useState<string | null>(null);
  const [pendingRespond, setPendingRespond] = useState<Incident | null>(null);
  const [mode, setMode] = useState<OperatingMode | null>(null);

  useEffect(() => {
    loadIncidents();
  }, [refreshSignal]);

  useEffect(() => {
    // The preflight has to state the real execution mode, not assume shadow.
    fetchOperatingMode().then(setMode).catch(() => setMode(null));
  }, [refreshSignal]);

  async function loadIncidents() {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchIncidents(25);
      const hydrated = await Promise.all(
        data.map((incident) =>
          incident.status === "detected" || incident.status === "responding"
            ? fetchIncidentDetail(incident.id).catch(() => incident)
            : Promise.resolve(incident),
        ),
      );
      setIncidents(hydrated);
    } catch (err: any) {
      setError(err.message || "Failed to load incidents");
    } finally {
      setLoading(false);
    }
  }

  async function handleScan() {
    setScanning(true);
    setScanResult(null);
    try {
      const res = await triggerIncidentScan();
      setScanResult(
        `Anomaly scan completed: ${res.alarms} active alarms found, ${res.incidents_created} incidents created, ${res.interventions} interventions initiated.`
      );
      await loadIncidents();
    } catch (err: any) {
      setError(`Scan failed: ${err.message}`);
    } finally {
      setScanning(false);
    }
  }

  async function handleRespond(incidentId: string) {
    setPendingRespond(null);
    setRespondingId(incidentId);
    setResponseResult(null);
    try {
      const res = await triggerIncidentResponse(incidentId);
      setResponseResult(
        res.batch_executed > 0
          ? `Batch response completed. ${res.batch_executed} policy-approved intervention(s) created from ${res.candidates_considered} candidate(s).`
          : `No interventions were dispatched. ${res.reason ?? "No eligible opportunity was found for this segment."}`,
      );
      await loadIncidents();
    } catch (err: any) {
      setError(`Batch response failed: ${err.message}`);
    } finally {
      setRespondingId(null);
    }
  }

  const activeIncidents = incidents.filter(
    (i) => i.status === "detected" || i.status === "responding"
  );
  const resolvedIncidents = incidents.filter((i) => i.status === "resolved");

  const liveExecution = mode?.customer_side_effects_enabled === true;
  const remainingBudget = pendingRespond
    ? pendingRespond.intervention_budget - pendingRespond.interventions_executed
    : 0;

  return (
    <div className="incidents-container">
      {pendingRespond && (
        <ConfirmAction
          title="Dispatch intervention batch"
          summary={
            `Every eligible failure in this segment will be run through diagnosis, ` +
            `prediction and the policy engine. Policy-approved actions execute immediately ` +
            `and interventions that reach a customer cannot be recalled.`
          }
          facts={[
            { label: "Segment", value: pendingRespond.title },
            {
              label: "Batch size",
              value: `up to ${Math.min(10, remainingBudget)} opportunities ` +
                     `(${remainingBudget} left of ${pendingRespond.intervention_budget})`,
            },
            { label: "Revenue at risk", value: inr(pendingRespond.revenue_at_risk_minor) },
            {
              label: "Execution mode",
              value: liveExecution
                ? `LIVE — ${mode?.razorpay_mode ?? "test"} mode, customers will be contacted`
                : "Observe only — actions are recorded, no customer is contacted",
              emphasis: true,
            },
          ]}
          confirmLabel={liveExecution ? "Contact customers" : "Run, observe only"}
          danger={liveExecution}
          safeNote={
            liveExecution
              ? null
              : "Observe-only mode is on, so this batch produces evidence only. Nothing is sent."
          }
          busy={respondingId === pendingRespond.id}
          onConfirm={() => handleRespond(pendingRespond.id)}
          onCancel={() => setPendingRespond(null)}
        />
      )}
      {/* Header Actions */}
      <div className="panel panel-header-row">
        <div>
          <h2>Incident Management & Automated Outage Response</h2>
          <span className="panel-sub">
            Real-time multi-detector anomaly scan (Window baseline + z-score + CUSUM)
          </span>
        </div>

        <div className="btn-group">
          <button
            type="button"
            className="btn btn-primary"
            onClick={handleScan}
            disabled={scanning}
          >
            {scanning ? "Scanning pipeline" : "Scan for anomalies"}
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={loadIncidents}
            disabled={loading}
          >
            <Icon name="refresh" size={15} className={loading ? "is-spinning" : undefined} />{loading ? "Loading" : "Refresh"}
          </button>
        </div>
      </div>

      {scanResult && <div className="alert-box alert-info">{scanResult}</div>}
      {responseResult && <div className="alert-box alert-info" role="status">{responseResult}</div>}
      {error && <div className="alert-box alert-error">{error}</div>}

      {/* Active Incidents Section */}
      <div className="section-block">
        <h3 className="section-title">
          Active Outages & Degradations ({activeIncidents.length})
        </h3>

        {activeIncidents.length === 0 ? (
          <div className="panel empty-state">
            <p className="empty-title"><span className="empty-marker" aria-hidden="true" />No active bank or payment method outages detected.</p>
            <span className="empty-hint">
              All payment segments are operating within normal baseline error bounds. You can trigger an incident simulation via the Simulation Console.
            </span>
          </div>
        ) : (
          <div className="incident-cards-grid">
            {activeIncidents.map((incident) => {
              const budgetPct =
                incident.intervention_budget > 0
                  ? (incident.interventions_executed / incident.intervention_budget) * 100
                  : 0;

              return (
                <div
                  key={incident.id}
                  className={`incident-card incident-card-${incident.severity} inverse`}
                >
                  <div className="incident-card-top">
                    <div className="incident-badge-group">
                      <span className={`chip chip-sev-${incident.severity}`}>
                        {incident.severity.toUpperCase()} SEVERITY
                      </span>
                      <span className={`status-pill st-${incident.status}`}>
                        {incident.status.toUpperCase()}
                      </span>
                    </div>
                    <span className="incident-time">
                      Started: {new Date(incident.started_at).toLocaleTimeString("en-IN")}
                    </span>
                  </div>

                  <h3 className="incident-title">{incident.title}</h3>
                  <div className="incident-segment">
                    Segment: <strong>{incident.bank ?? "All Banks"}</strong> ·{" "}
                    <strong>{incident.method?.toUpperCase() ?? "ALL METHODS"}</strong>
                  </div>

                  <div className="incident-metrics-grid">
                    <div className="inc-metric">
                      <span className="inc-metric-lbl">Revenue at Risk</span>
                      <span className="inc-metric-val risk-val">
                        {inr(incident.revenue_at_risk_minor)}
                      </span>
                    </div>
                    <div className="inc-metric">
                      <span className="inc-metric-lbl">Failures in Window</span>
                      <span className="inc-metric-val">{incident.affected_failures}</span>
                    </div>
                    <div className="inc-metric">
                      <span className="inc-metric-lbl">Intervention Budget</span>
                      <span className="inc-metric-val">
                        {incident.interventions_executed} / {incident.intervention_budget}
                      </span>
                    </div>
                  </div>

                  {/* Budget Progress Bar */}
                  <div className="incident-budget-bar">
                    <div className="budget-bar-label">
                      <span>Interventions Executed Under Cap</span>
                      <span>{budgetPct.toFixed(0)}%</span>
                    </div>
                    <div className="progress-track">
                      <div
                        className="progress-fill fill-good"
                        style={{ width: `${budgetPct}%` }}
                      />
                    </div>
                  </div>

                  {/* Diagnostic stats if present */}
                  {incident.detection_stats && (
                    <div className="inc-diag-box">
                      <span className="diag-title">DETECTION EVIDENCE</span>
                      <div className="diag-chips">
                        {(incident.detection_stats.detectors_fired as string[] || []).map((det) => (
                          <span key={det} className="chip chip-purple">
                            {det.replace("_", " ").toUpperCase()}
                          </span>
                        ))}
                        {incident.detection_stats.z_score !== undefined && (
                          <span className="diag-tag">
                            z-score: {Number(incident.detection_stats.z_score).toFixed(2)}
                          </span>
                        )}
                        {incident.detection_stats.recent_failure_rate !== undefined && (
                          <span className="diag-tag">
                            Failure Rate: {(Number(incident.detection_stats.recent_failure_rate) * 100).toFixed(0)}% (vs {(Number(incident.detection_stats.baseline_failure_rate) * 100).toFixed(0)}% base)
                          </span>
                        )}
                      </div>
                    </div>
                  )}

                  <div className="incident-card-actions">
                    <button
                      type="button"
                      className="btn btn-primary"
                      onClick={() => setPendingRespond(incident)}
                      disabled={
                        respondingId === incident.id ||
                        incident.interventions_executed >= incident.intervention_budget
                      }
                    >
                      {respondingId === incident.id
                        ? "Executing Batch…"
                        : incident.interventions_executed >= incident.intervention_budget
                        ? "Budget Exhausted"
                        : "Dispatch intervention batch"}
                    </button>
                    {incident.affected_opportunities && incident.affected_opportunities.length > 0 && onSelectOpportunity && (
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => onSelectOpportunity(incident.affected_opportunities![0].id)}
                      >
                        Inspect affected opportunity
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Resolved Incidents History */}
      <div className="section-block">
        <h3 className="section-title">Incident History ({resolvedIncidents.length})</h3>

        <div className="panel table-responsive">
          <table className="table">
            <thead>
              <tr>
                <th>Title</th>
                <th>Method / Bank</th>
                <th>Severity</th>
                <th>Revenue at Risk</th>
                <th>Interventions Executed</th>
                <th>Started At</th>
                <th>Resolved At</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {resolvedIncidents.length === 0 ? (
                <tr>
                  <td colSpan={8} className="empty-cell">
                    No resolved incident history recorded yet.
                  </td>
                </tr>
              ) : (
                resolvedIncidents.map((inc) => (
                  <tr key={inc.id}>
                    <td><strong>{inc.title}</strong></td>
                    <td>
                      <code>{inc.bank ?? "—"} / {inc.method?.toUpperCase() ?? "—"}</code>
                    </td>
                    <td>
                      <span className={`chip chip-sev-${inc.severity}`}>
                        {inc.severity.toUpperCase()}
                      </span>
                    </td>
                    <td className="amount-cell">{inr(inc.revenue_at_risk_minor)}</td>
                    <td className="num">
                      <strong>{inc.interventions_executed}</strong> / {inc.intervention_budget}
                    </td>
                    <td className="time-cell">
                      {new Date(inc.started_at).toLocaleTimeString("en-IN", {
                        day: "numeric",
                        month: "short",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </td>
                    <td className="time-cell">
                      {inc.resolved_at
                        ? new Date(inc.resolved_at).toLocaleTimeString("en-IN", {
                            day: "numeric",
                            month: "short",
                            hour: "2-digit",
                            minute: "2-digit",
                          })
                        : "—"}
                    </td>
                    <td>
                      <span className="status-pill st-resolved">RESOLVED</span>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
