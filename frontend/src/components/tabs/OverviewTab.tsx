import { useState } from "react";
import { kindClass, kindLabel } from "../../api/stream";
import type { FeedItem, Summary } from "../../api/types";
import { Icon } from "../Icon";

interface OverviewTabProps {
  summary: Summary | null;
  feed: FeedItem[];
  onClearFeed: () => void;
  onSelectOpportunity?: (id: string) => void;
}

export function OverviewTab({
  summary,
  feed,
  onClearFeed,
  onSelectOpportunity,
}: OverviewTabProps) {
  const [filterKind, setFilterKind] = useState<string>("all");
  const [searchTerm, setSearchTerm] = useState<string>("");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const filteredFeed = feed.filter((item) => {
    if (filterKind !== "all" && !item.kind.includes(filterKind)) return false;
    if (searchTerm) {
      const term = searchTerm.toLowerCase();
      const match =
        item.headline.toLowerCase().includes(term) ||
        item.detail.toLowerCase().includes(term) ||
        item.kind.toLowerCase().includes(term);
      if (!match) return false;
    }
    return true;
  });

  const totalPayments = summary
    ? Object.values(summary.payments_by_status).reduce((a, b) => a + b, 0)
    : 0;

  return (
    <div className="overview-layout">
      <section className="panel feed-panel">
        <div className="panel-header">
          <div>
            <div className="panel-title-line"><h2>Activity stream</h2></div>
            <span className="panel-sub">
              {filteredFeed.length} live lifecycle and decision events
            </span>
          </div>

          <div className="feed-controls">
            <label className="search-field"><Icon name="search" size={15} /><span className="sr-only">Search stream events</span><input type="text" placeholder="Search stream events" value={searchTerm} onChange={(e) => setSearchTerm(e.target.value)} /></label>

            <select
              className="select-filter"
              value={filterKind}
              onChange={(e) => setFilterKind(e.target.value)}
            >
              <option value="all">All Events</option>
              <option value="opportunity">Opportunities</option>
              <option value="incident">Incidents</option>
              <option value="decision">Decisions & Policy</option>
              <option value="payment">Payments</option>
              <option value="razorpay">Webhooks</option>
              <option value="verification">Verification</option>
            </select>

            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={onClearFeed}
              title="Clear feed"
            >
              <Icon name="x" size={14} /> Clear
            </button>
          </div>
        </div>

        {filteredFeed.length === 0 ? (
          <div className="empty-state">
            <p>No events matching filter.</p>
            <span className="empty-hint">
              Send a test webhook or start demo traffic to see activity here.
            </span>
          </div>
        ) : (
          <ul className="feed-list">
            {filteredFeed.map((item) => {
              const isExpanded = expandedId === item.id;
              const rawOpportunityId = item.rawData?.opportunity_id;
              const oppId =
                typeof rawOpportunityId === "string" &&
                /^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(rawOpportunityId)
                  ? rawOpportunityId
                  : null;

              return (
                <li key={item.id} className={`feed-item ${isExpanded ? "feed-item-expanded" : ""}`}>
                  <div className="feed-row-wrap">
                    <button type="button" className="feed-row feed-row-button" onClick={() => setExpandedId(isExpanded ? null : item.id)} aria-expanded={isExpanded}>
                      <span className="feed-time">{item.time}</span>
                      <span className={kindClass(item.kind)}>{kindLabel(item.kind)}</span>
                      <span className="feed-headline">{item.headline}</span>
                      <span className="feed-detail">{item.detail}</span>
                      <Icon name={isExpanded ? "chevron-right" : "chevron-right"} size={15} className={isExpanded ? "feed-chevron feed-chevron-open" : "feed-chevron"} />
                    </button>
                    {oppId && onSelectOpportunity && <button type="button" className="btn-inspect" onClick={() => onSelectOpportunity(oppId)}>Inspect decision</button>}
                  </div>

                  {isExpanded && item.rawData && (
                    <div className="feed-raw-data" onClick={(e) => e.stopPropagation()}>
                      <div className="raw-header">Stream payload</div>
                      <pre>{JSON.stringify(item.rawData, null, 2)}</pre>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <aside className="side-column">
        <section className="panel status-panel">
          <div className="panel-header">
            <div className="panel-title-line"><h3>Payments today</h3></div>
            <span className="badge-count">{totalPayments} total</span>
          </div>

          {!summary || Object.keys(summary.payments_by_status).length === 0 ? (
            <p className="empty">No payments recorded today.</p>
          ) : (
            <div className="status-bars">
              {Object.entries(summary.payments_by_status).map(([status, count]) => {
                const pct = totalPayments > 0 ? (count / totalPayments) * 100 : 0;
                return (
                  <div key={status} className="status-row">
                    <div className="status-info">
                      <span className={`status-pill st-${status}`}>{status.replace(/_/g, " ")}</span>
                      <span className="status-count">
                        <strong>{count}</strong> ({pct.toFixed(1)}%)
                      </span>
                    </div>
                    <div className="progress-track">
                      <div
                        className={`progress-fill st-fill-${status}`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>

        <section className="panel info-panel">
          <div className="panel-title-line"><h3>How recovery works</h3></div>
          <ol className="pipeline-steps">
            <li>
              <strong>Observe.</strong> Verify Razorpay webhooks and preserve their source.
            </li>
            <li>
              <strong>Diagnose.</strong> Classify retryable, instrument, and funds failures.
            </li>
            <li>
              <strong>Decide.</strong> Score only actions that passed offline quality gates.
            </li>
            <li>
              <strong>Protect.</strong> Enforce value caps, contact budgets, and kill switches.
            </li>
            <li>
              <strong>Act.</strong> In shadow mode, record the action without contacting customers.
            </li>
            <li>
              <strong>Verify.</strong> Reconcile outcomes and separate natural recovery.
            </li>
          </ol>
        </section>
      </aside>
    </div>
  );
}
