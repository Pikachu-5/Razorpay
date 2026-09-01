import { useCallback, useEffect, useState } from "react";
import { fetchOpportunities, fetchOpportunityQueue } from "../../api/client";
import type { Opportunity } from "../../api/types";
import { inr } from "../../utils/format";
import { Icon } from "../Icon";
import { RecoveryQueue } from "../RecoveryQueue";

interface OpportunitiesTabProps {
  onSelectOpportunity: (id: string) => void;
  refreshSignal?: number;
}

const STATUS_FILTERS = [
  { key: "all", label: "All Opportunities" },
  { key: "open", label: "Open" },
  { key: "intervention_pending", label: "Intervention Pending" },
  { key: "closed_not_viable", label: "Closed (Not Viable)" },
  { key: "recovered_intervention", label: "Recovered (Intervention)" },
  { key: "recovered_natural", label: "Recovered (Natural)" },
  { key: "closed_no_response", label: "Closed (No Response)" },
  { key: "closed_expired", label: "Closed (Expired)" },
  { key: "escalated", label: "Escalated" },
];

export function OpportunitiesTab({
  onSelectOpportunity,
  refreshSignal,
}: OpportunitiesTabProps) {
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [loading, setLoading] = useState(false);
  const [statusFilter, setStatusFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [limit, setLimit] = useState(50);
  const [error, setError] = useState<string | null>(null);
  // The queue is deliberately independent of the explorer's status filter: it
  // answers "what needs me now", which must not change when someone narrows the
  // table below to inspect something else.
  const [queueItems, setQueueItems] = useState<Opportunity[]>([]);

  const loadOpportunities = useCallback(async () => {
    setLoading(true);
    setError(null);
    const [table, queue] = await Promise.allSettled([
      fetchOpportunities(statusFilter, limit),
      fetchOpportunityQueue(200),
    ]);
    if (table.status === "fulfilled") setOpportunities(table.value);
    if (queue.status === "fulfilled") setQueueItems(queue.value);
    const failure = [table, queue].find((r) => r.status === "rejected");
    setError(
      failure && failure.status === "rejected"
        ? failure.reason instanceof Error
          ? failure.reason.message
          : "Failed to load opportunities"
        : null,
    );
    setLoading(false);
  }, [statusFilter, limit]);

  useEffect(() => {
    const initialLoad = setTimeout(() => void loadOpportunities(), 0);
    return () => clearTimeout(initialLoad);
  }, [loadOpportunities, refreshSignal]);

  const filtered = opportunities.filter((o) => {
    if (!search) return true;
    const term = search.toLowerCase();
    return (
      o.id.toLowerCase().includes(term) ||
      o.category.toLowerCase().includes(term) ||
      (o.best_action && o.best_action.toLowerCase().includes(term)) ||
      o.status.toLowerCase().includes(term)
    );
  });

  return (
    <div className="opportunities-stack">
      <RecoveryQueue opportunities={queueItems} onSelectOpportunity={onSelectOpportunity} />

      <div className="panel opportunities-panel">
      <div className="panel-header">
        <div>
          <h2>All opportunities</h2>
          <span className="panel-sub">
            Full ledger, ranked by expected recovery value · {filtered.length} loaded
          </span>
        </div>

        <div className="filter-actions">
          <input
            type="text"
            className="input-search"
            placeholder="Search by ID, action, category…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />

          <select
            className="select-filter"
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
          >
            <option value={25}>25 rows</option>
            <option value={50}>50 rows</option>
            <option value={100}>100 rows</option>
          </select>

          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={loadOpportunities}
            disabled={loading}
          >
            <Icon name="refresh" size={15} className={loading ? "is-spinning" : undefined} />{loading ? "Loading" : "Refresh"}
          </button>
        </div>
      </div>

      {/* Status Filter Chips */}
      <div className="status-filter-chips">
        {STATUS_FILTERS.map((filter) => (
          <button
            key={filter.key}
            type="button"
            className={`filter-chip ${statusFilter === filter.key ? "filter-chip-active" : ""}`}
            onClick={() => setStatusFilter(filter.key)}
          >
            {filter.label}
          </button>
        ))}
      </div>

      {error && <div className="alert-box alert-error">{error}</div>}

      {/* Opportunities Table */}
      <div className="table-responsive">
        <table className="table">
          <thead>
            <tr>
              <th>Opportunity ID</th>
              <th>Category</th>
              <th>Amount</th>
              <th>Status</th>
              <th>Best Action</th>
              <th>Expected Recovery (EV)</th>
              <th>Contact Attempts</th>
              <th>Window Ends At</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {loading && opportunities.length === 0 ? (
              <tr>
                <td colSpan={9} className="loading-cell">
                  Loading recovery opportunities…
                </td>
              </tr>
            ) : filtered.length === 0 ? (
              <tr>
                <td colSpan={9} className="empty-cell">
                  No opportunities found for status <code>"{statusFilter}"</code>.
                </td>
              </tr>
            ) : (
              filtered.map((opp) => (
                <tr key={opp.id}>
                  <td className="mono">
                    <button
                      type="button"
                      className="link-button mono"
                      onClick={() => onSelectOpportunity(opp.id)}
                    >
                      {opp.id.slice(0, 8)}…
                    </button>
                  </td>
                  <td>
                    <span className="category-tag">{opp.category}</span>
                  </td>
                  <td className="amount-cell">{inr(opp.amount_minor)}</td>
                  <td>
                    <span className={`status-pill st-${opp.status}`}>
                      {opp.status.replace(/_/g, " ")}
                    </span>
                  </td>
                  <td>
                    {opp.best_action ? (
                      <code className="action-pill">{opp.best_action}</code>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td className="ev-val">
                    {inr(opp.expected_recovery_minor ?? 0)}
                  </td>
                  <td className="num">
                    <span
                      className={`budget-badge ${
                        opp.contact_attempts >= 3 ? "budget-exhausted" : ""
                      }`}
                    >
                      {opp.contact_attempts} / 3
                    </span>
                  </td>
                  <td className="time-cell">
                    {new Date(opp.window_ends_at).toLocaleTimeString("en-IN", {
                      hour: "2-digit",
                      minute: "2-digit",
                      day: "numeric",
                      month: "short",
                    })}
                  </td>
                  <td>
                    <button
                      type="button"
                      className="btn btn-primary btn-xs"
                      onClick={() => onSelectOpportunity(opp.id)}
                    >
                      Inspect audit
                    </button>
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
