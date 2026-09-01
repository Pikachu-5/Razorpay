import { ALL_EVENT_KINDS, type FeedItem, type StreamEnvelope } from "./types";
import { API_BASE } from "./client";

let seq = 0;

function formatTime(iso?: string): string {
  const d = iso ? new Date(iso) : new Date();
  return d.toLocaleTimeString("en-IN", { hour12: false });
}

const KIND_LABELS: Record<string, string> = {
  "razorpay.event": "WEBHOOK",
  "payment.recorded": "PAYMENT",
  "payment.updated": "PAYMENT",
  "opportunity.created": "OPPORTUNITY",
  "opportunity.expired": "EXPIRED",
  "opportunity.resolved": "RECOVERED",
  "diagnosis.completed": "DIAGNOSIS",
  "prediction.completed": "ML PREDICT",
  "policy.evaluated": "POLICY",
  "decision.finalized": "DECISION",
  "decision.failed": "DECISION ERROR",
  "incident.detected": "INCIDENT",
  "incident.response": "INCIDENT RESP",
  "incident.resolved": "INCIDENT RESOLVED",
  "verification.completed": "VERIFIED",
};

export function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind.replace(".", " ").toUpperCase();
}

export function kindClass(kind: string): string {
  switch (kind) {
    case "opportunity.created":
    case "incident.detected":
      return "chip chip-risk";
    case "opportunity.resolved":
    case "verification.completed":
    case "incident.resolved":
      return "chip chip-good";
    case "razorpay.event":
    case "payment.recorded":
      return "chip chip-info";
    case "policy.evaluated":
    case "opportunity.expired":
    case "decision.failed":
      return "chip chip-warn";
    case "decision.finalized":
    case "incident.response":
    case "prediction.completed":
      return "chip chip-purple";
    default:
      return "chip";
  }
}

function inr(minor?: number): string {
  if (minor === undefined || minor === null) return "—";
  return `₹${(Number(minor) / 100).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

function describe(envelope: StreamEnvelope): { headline: string; detail: string } {
  const d = (envelope.data ?? {}) as Record<string, any>;
  switch (envelope.kind) {
    case "razorpay.event":
      return {
        headline: `${d.event_type ?? "Webhook"} received`,
        detail: `${d.entity_type ?? ""} ${d.entity_id ?? d.event_uid ?? ""}`.trim(),
      };
    case "decision.failed":
      return {
        headline: "Decision pipeline escalated",
        detail: String(d.error ?? "Unknown decision error"),
      };
    case "payment.recorded":
      return {
        headline: `Payment ${d.status}`,
        detail: `${d.razorpay_payment_id ?? ""} · ${inr(d.amount_minor)} · ${d.method ?? "?"}${d.bank ? ` · ${d.bank}` : ""}`,
      };
    case "payment.updated":
      return {
        headline: `Payment changed from ${d.previous_status} to ${d.status}`,
        detail: String(d.razorpay_payment_id ?? ""),
      };
    case "opportunity.created":
      return {
        headline: `Recovery Opportunity Created · ${inr(d.amount_minor)}`,
        detail: `ID: ${String(d.opportunity_id ?? "").slice(0, 8)}… · Group: ${d.experiment_group ?? "standard"}`,
      };
    case "opportunity.expired":
      return {
        headline: `Opportunity Window Expired`,
        detail: `ID: ${String(d.opportunity_id ?? "").slice(0, 8)}… · Status: ${d.final_status ?? "closed"}`,
      };
    case "opportunity.resolved":
      return {
        headline: `Recovered Naturally · ${inr(d.amount_minor)}`,
        detail: `Payment: ${d.razorpay_payment_id ?? ""}`,
      };
    case "diagnosis.completed":
      return {
        headline: `Diagnosis: ${d.classification ?? ""}`,
        detail: `Conf: ${Math.round((d.confidence ?? 0) * 100)}% · ${d.summary ?? ""}`,
      };
    case "prediction.completed":
      return {
        headline: `ML Model ${d.model_version ?? "v2"}: Best action ${d.best_action ?? ""}`,
        detail: `Expected Recovery: ${inr(d.expected_recovery_minor)} ${d.degraded ? "(fallback mode)" : ""}`,
      };
    case "policy.evaluated":
      return {
        headline: `Policy Gate: ${d.allowed ? "ALLOWED" : "BLOCKED"} (${d.action ?? ""})`,
        detail: d.allowed ? "Passed all deterministic safety rules" : `Failed: ${(d.rules_failed ?? []).join(", ")}`,
      };
    case "decision.finalized":
      return {
        headline: `Decision: ${d.action ?? ""} [${d.final_status ?? ""}]`,
        detail: `Latency: ${d.latency_ms ?? 0}ms · Opp: ${String(d.opportunity_id ?? "").slice(0, 8)}…`,
      };
    case "incident.detected":
      return {
        headline: `Incident: ${d.title ?? "Anomaly"} [${(d.severity ?? "").toUpperCase()}]`,
        detail: `Risk: ${inr(d.revenue_at_risk_minor)} · Detectors: ${(d.detectors ?? []).join(", ")}`,
      };
    case "incident.response":
      return {
        headline: `Incident Batch Response: ${d.batch_executed ?? 0} interventions`,
        detail: `Evaluated ${d.candidates_considered ?? 0} candidates · ${d.note ?? ""}`,
      };
    case "incident.resolved":
      return {
        headline: `Incident Resolved`,
        detail: `Total interventions executed: ${d.interventions_executed ?? 0}`,
      };
    case "verification.completed":
      return {
        headline: `Attribution Verified: ${d.outcome ?? "recovered"}`,
        detail: `Recovered: ${inr(d.amount_minor)} via ${d.action ?? "intervention"}`,
      };
    default:
      return {
        headline: envelope.kind,
        detail: JSON.stringify(d).slice(0, 100),
      };
  }
}

export function toFeedItem(envelope: StreamEnvelope): FeedItem {
  const { headline, detail } = describe(envelope);
  return {
    id: `f${seq++}_${Date.now()}`,
    time: formatTime(envelope.ts),
    kind: envelope.kind,
    headline,
    detail,
    rawData: envelope.data,
  };
}

export function connectStream(
    onItem: (item: FeedItem) => void,
    onAny: () => void,
    onConnectionChange?: (connected: boolean) => void,
): () => void {
  const source = new EventSource(`${API_BASE}/api/stream`);

  source.onopen = () => onConnectionChange?.(true);
  source.onerror = () => onConnectionChange?.(false);
  source.addEventListener("connected", () => onConnectionChange?.(true));
  
  for (const kind of ALL_EVENT_KINDS) {
    source.addEventListener(kind, (e: MessageEvent<string>) => {
      try {
        const envelope: StreamEnvelope = JSON.parse(e.data);
        onItem(toFeedItem(envelope));
        onAny();
      } catch (err) {
        console.error("Stream parse error:", err);
      }
    });
  }

  // Also listen to generic message if sent without event header
  source.onmessage = (e) => {
    try {
      const envelope: StreamEnvelope = JSON.parse(e.data);
      onItem(toFeedItem(envelope));
      onAny();
    } catch {
      // ping / comment
    }
  };

  return () => source.close();
}
