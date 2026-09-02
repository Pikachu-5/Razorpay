import { useState } from "react";
import { kindClass, kindLabel } from "../../api/stream";
import type { FeedItem } from "../../api/types";
import { Icon } from "../Icon";

interface OverviewTabProps {
  feed: FeedItem[];
  onClearFeed: () => void;
  onSelectOpportunity?: (id: string) => void;
}

export function OverviewTab({
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
    </div>
  );
}
