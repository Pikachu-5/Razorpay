import type {
  DecisionAudit,
  Incident,
  MlStatus,
  ModelCard,
  Opportunity,
  OpportunityDetail,
  PolicyConfig,
  SimulationConfig,
  SimulationStatus,
  Summary,
  RazorpayState,
  ReconciliationResult,
} from "./types";

export const API_BASE = import.meta.env.VITE_API_BASE ?? "";

/**
 * The operator key is supplied at *runtime* and held only for this browser tab.
 *
 * It used to be read from `import.meta.env.VITE_CONTROL_PLANE_KEY`, which Vite
 * inlines as a string literal at build time -- so any deployment that set it
 * shipped the control-plane key inside a public JavaScript bundle, readable by
 * every visitor. A key that authenticates operator actions cannot survive being
 * compiled into the client, so it is no longer read from the build at all.
 *
 * `sessionStorage` keeps it out of the bundle, out of the URL, and out of the
 * profile once the tab closes.
 */
const KEY_STORAGE = "recover_control_plane_key";

export function getControlPlaneKey(): string {
  try {
    return sessionStorage.getItem(KEY_STORAGE) ?? "";
  } catch {
    return "";
  }
}

export function setControlPlaneKey(key: string): void {
  try {
    if (key) sessionStorage.setItem(KEY_STORAGE, key);
    else sessionStorage.removeItem(KEY_STORAGE);
  } catch {
    // Private browsing or storage disabled: the operator re-enters the key on
    // the next action rather than the console failing outright.
  }
}

function mutationHeaders(json: boolean = false): HeadersInit {
  const key = getControlPlaneKey();
  return {
    ...(json ? { "Content-Type": "application/json" } : {}),
    ...(key ? { "X-Control-Plane-Key": key } : {}),
  };
}

/** Raised when the control plane wants an operator key this session lacks. */
export class ControlPlaneAuthError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ControlPlaneAuthError";
  }
}

async function handleRes<T>(res: Response, name: string): Promise<T> {
  if (!res.ok) {
    const errorText = await res.text().catch(() => "");
    if (res.status === 401 || res.status === 503) {
      throw new ControlPlaneAuthError(
        "This install requires an operator key. Add it with " +
          "sessionStorage.setItem('recover_control_plane_key', '<key>') and retry.",
      );
    }
    throw new Error(`${name} failed (${res.status}): ${errorText || res.statusText}`);
  }
  return res.json();
}

export async function fetchSummary(includeSynthetic = false): Promise<Summary> {
  const qs = includeSynthetic ? "?include_synthetic=true" : "";
  const res = await fetch(`${API_BASE}/api/metrics/summary${qs}`);
  return handleRes<Summary>(res, "fetchSummary");
}

export async function fetchFailureMix(): Promise<import("./types").FailureMix> {
  const res = await fetch(`${API_BASE}/api/metrics/failure-mix`);
  return handleRes(res, "fetchFailureMix");
}

export async function fetchRazorpayState(): Promise<RazorpayState> {
  const res = await fetch(`${API_BASE}/api/razorpay/state`);
  return handleRes<RazorpayState>(res, "fetchRazorpayState");
}

export async function runReconciliation(): Promise<ReconciliationResult> {
  const res = await fetch(`${API_BASE}/api/reconciliation/run`, {
    method: "POST",
    headers: mutationHeaders(),
  });
  return handleRes<ReconciliationResult>(res, "runReconciliation");
}

export async function fetchRecentEvents(limit: number = 50): Promise<Array<{
  event_uid: string;
  event_type: string;
  entity_type: string | null;
  entity_id: string | null;
  received_at: string;
}>> {
  const res = await fetch(`${API_BASE}/api/events/recent?limit=${limit}`);
  return handleRes(res, "fetchRecentEvents");
}

export async function fetchOpportunities(status?: string, limit: number = 50): Promise<Opportunity[]> {
  const params = new URLSearchParams();
  if (status && status !== "all") params.set("status", status);
  if (limit) params.set("limit", String(limit));
  const res = await fetch(`${API_BASE}/api/opportunities?${params.toString()}`);
  return handleRes<Opportunity[]>(res, "fetchOpportunities");
}

/** Unresolved work for the triage queue, filtered server-side by status. */
export async function fetchOpportunityQueue(limit: number = 200): Promise<Opportunity[]> {
  const res = await fetch(`${API_BASE}/api/opportunities/queue?limit=${limit}`);
  return handleRes<Opportunity[]>(res, "fetchOpportunityQueue");
}

export async function fetchRecentDecisions(limit: number = 25): Promise<DecisionAudit[]> {
  const res = await fetch(`${API_BASE}/api/opportunities/recent-decisions?limit=${limit}`);
  return handleRes<DecisionAudit[]>(res, "fetchRecentDecisions");
}

export async function fetchOpportunityDetail(id: string): Promise<OpportunityDetail> {
  const res = await fetch(`${API_BASE}/api/opportunities/${id}`);
  return handleRes<OpportunityDetail>(res, "fetchOpportunityDetail");
}

export async function redecideOpportunity(id: string): Promise<Record<string, unknown>> {
  const res = await fetch(`${API_BASE}/api/opportunities/${id}/decide`, {
    method: "POST",
    headers: mutationHeaders(),
  });
  return handleRes<Record<string, unknown>>(res, "redecideOpportunity");
}

export async function fetchIncidents(limit: number = 25): Promise<Incident[]> {
  const res = await fetch(`${API_BASE}/api/incidents?limit=${limit}`);
  return handleRes<Incident[]>(res, "fetchIncidents");
}

export async function fetchIncidentDetail(id: string): Promise<Incident> {
  const res = await fetch(`${API_BASE}/api/incidents/${id}`);
  return handleRes<Incident>(res, "fetchIncidentDetail");
}

export async function triggerIncidentScan(): Promise<{ alarms: number; incidents_created: number; interventions: number }> {
  const res = await fetch(`${API_BASE}/api/incidents/scan`, {
    method: "POST",
    headers: mutationHeaders(),
  });
  return handleRes(res, "triggerIncidentScan");
}

export async function triggerIncidentResponse(id: string): Promise<{
  incident_id: string;
  batch_executed: number;
  candidates_considered: number;
  candidates_found: number;
  reason: string | null;
}> {
  const res = await fetch(`${API_BASE}/api/incidents/${id}/respond`, {
    method: "POST",
    headers: mutationHeaders(),
  });
  return handleRes(res, "triggerIncidentResponse");
}

export async function fetchSimulationStatus(): Promise<SimulationStatus> {
  const res = await fetch(`${API_BASE}/api/simulation/status`);
  return handleRes<SimulationStatus>(res, "fetchSimulationStatus");
}

export async function startSimulation(config: SimulationConfig): Promise<Record<string, unknown>> {
  const res = await fetch(`${API_BASE}/api/simulation/start`, {
    method: "POST",
    headers: mutationHeaders(true),
    body: JSON.stringify(config),
  });
  return handleRes(res, "startSimulation");
}

export async function stopSimulation(): Promise<Record<string, unknown>> {
  const res = await fetch(`${API_BASE}/api/simulation/stop`, {
    method: "POST",
    headers: mutationHeaders(),
  });
  return handleRes(res, "stopSimulation");
}

export async function fetchOperatingMode(): Promise<import("./types").OperatingMode> {
  const res = await fetch(`${API_BASE}/api/policy/operating-mode`);
  return handleRes(res, "fetchOperatingMode");
}

export async function setShadowMode(enabled: boolean): Promise<import("./types").OperatingMode> {
  const res = await fetch(`${API_BASE}/api/policy/shadow-mode`, {
    method: "POST",
    headers: mutationHeaders(true),
    body: JSON.stringify({ enabled }),
  });
  return handleRes(res, "setShadowMode");
}

export async function fetchMlStatus(): Promise<MlStatus> {
  const res = await fetch(`${API_BASE}/api/ml/status`);
  return handleRes<MlStatus>(res, "fetchMlStatus");
}

export async function fetchModelCard(): Promise<ModelCard> {
  const res = await fetch(`${API_BASE}/api/ml/model-card`);
  return handleRes<ModelCard>(res, "fetchModelCard");
}

export async function fetchPolicyConfig(): Promise<PolicyConfig> {
  const res = await fetch(`${API_BASE}/api/policy/config`);
  return handleRes<PolicyConfig>(res, "fetchPolicyConfig");
}

export async function fetchExperimentMetrics(
  includeSimulated = false,
): Promise<import("./types").ExperimentMetrics> {
  const qs = includeSimulated ? "?include_synthetic=true" : "";
  const res = await fetch(`${API_BASE}/api/metrics/experiment${qs}`);
  return handleRes<import("./types").ExperimentMetrics>(res, "fetchExperimentMetrics");
}

export async function fetchModelComparison(): Promise<import("./types").ModelComparison> {
  const res = await fetch(`${API_BASE}/api/ml/comparison`);
  return handleRes<import("./types").ModelComparison>(res, "fetchModelComparison");
}

export async function promoteModel(
  versionOrArtifact: string,
  force: boolean = false
): Promise<Record<string, unknown>> {
  const isArtifact = versionOrArtifact.endsWith(".pkl");
  const payload = isArtifact
    ? { artifact: versionOrArtifact, force }
    : { version: versionOrArtifact, force };
  const res = await fetch(`${API_BASE}/api/ml/promote`, {
    method: "POST",
    headers: mutationHeaders(true),
    body: JSON.stringify(payload),
  });
  return handleRes(res, "promoteModel");
}
