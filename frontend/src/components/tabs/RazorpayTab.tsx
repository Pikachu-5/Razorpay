import { useState } from "react";
import { runReconciliation } from "../../api/client";
import type { RazorpayState, RazorpayStateItem } from "../../api/types";
import { inr } from "../../utils/format";

type StateKey = "orders" | "payment_links" | "subscriptions" | "invoices" | "downtimes" | "revenue_adjustments";

const sections: Array<{ key: StateKey; label: string }> = [
  { key: "orders", label: "Orders" },
  { key: "payment_links", label: "Payment links" },
  { key: "subscriptions", label: "Subscriptions" },
  { key: "invoices", label: "Invoices" },
  { key: "downtimes", label: "Downtime" },
  { key: "revenue_adjustments", label: "Adjustments" },
];

function reference(item: RazorpayStateItem): string {
  return item.id ?? item.external_id ?? "—";
}

function amount(item: RazorpayStateItem): string {
  const value = item.amount_minor ?? item.amount_paid_minor ?? item.amount_due_minor;
  return value == null ? "—" : inr(value);
}

export function RazorpayTab({ state, onRefresh }: { state: RazorpayState | null; onRefresh: () => Promise<void> }) {
  const [active, setActive] = useState<StateKey>("orders");
  const [reconciling, setReconciling] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const rows = state?.[active] ?? [];

  const reconcile = async () => {
    setReconciling(true);
    setNotice(null);
    try {
      const result = await runReconciliation();
      const updated = Object.values(result.updated ?? {}).reduce((sum, value) => sum + value, 0);
      setNotice(`${updated} state records updated. ${result.errors?.length ?? 0} fetch errors.`);
      await onRefresh();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Reconciliation could not run.");
    } finally { setReconciling(false); }
  };

  return (
    <div className="razorpay-container">
      <section className="mode-banner">
        <div><strong>{state?.operating_mode.razorpay_mode ?? "test"} environment</strong><span>{state?.operating_mode.shadow_mode ?? true ? "Customer-facing actions are blocked. Decisions are observed only." : "Customer-facing execution is enabled."}</span></div>
        <button type="button" className="btn btn-primary" onClick={reconcile} disabled={reconciling}>{reconciling ? "Reconciling…" : "Reconcile now"}</button>
      </section>
      {notice && <div className="alert-box alert-info" role="status">{notice}</div>}

      <div className="state-tabs" role="tablist" aria-label="Razorpay lifecycle data">
        {sections.map((section) => <button key={section.key} type="button" role="tab" aria-selected={active === section.key} className={active === section.key ? "active" : ""} onClick={() => setActive(section.key)}>{section.label}<span>{state?.[section.key].length ?? 0}</span></button>)}
      </div>

      <section className="panel table-responsive">
        <div className="panel-header"><div><h3>{sections.find((section) => section.key === active)?.label}</h3><span className="panel-sub">Latest known state, repaired by bounded API reconciliation when webhooks are missed.</span></div></div>
        <table className="table">
          <thead><tr><th>Reference</th><th>Status</th><th>Amount</th><th>Details</th><th>Source</th></tr></thead>
          <tbody>
            {rows.length === 0 ? <tr><td colSpan={5} className="empty-cell">No {active.replace("_", " ")} recorded yet.</td></tr> : rows.map((item) => (
              <tr key={`${active}-${reference(item)}`}>
                <td className="mono">{reference(item)}</td>
                <td><span className={`status-pill st-${item.status}`}>{item.status}</span></td>
                <td className="amount-cell">{amount(item)}</td>
                <td className="muted">{item.method ?? item.kind ?? item.plan_id ?? item.subscription_id ?? (item.attempts != null ? `${item.attempts} attempts` : "—")}</td>
                <td><span className="source-tag">{item.source}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
