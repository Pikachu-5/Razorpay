import type { Opportunity } from "../api/types";
import { buildLanes, type QueueLane } from "../lib/recoveryQueueLanes";
import { inr } from "../utils/format";

interface RecoveryQueueProps {
  opportunities: Opportunity[];
  onSelectOpportunity: (id: string) => void;
}

function laneValue(lane: QueueLane): number {
  return lane.items.reduce((total, opp) => total + opp.amount_minor, 0);
}

function QueueRow({
  opp, onSelect,
}: { opp: Opportunity; onSelect: (id: string) => void }) {
  const reason = opp.closed_reason?.trim();
  return (
    <li className="queue-row">
      <button
        type="button"
        className="queue-row-button"
        onClick={() => onSelect(opp.id)}
        aria-label={`Inspect opportunity ${opp.id.slice(0, 8)}, ${inr(opp.amount_minor)}`}
      >
        <span className="queue-amount">{inr(opp.amount_minor)}</span>
        <span className="queue-meta">
          <code className="mono-id">{opp.id.slice(0, 8)}</code>
          {opp.best_action ? (
            <code className="action-pill-sm">{opp.best_action}</code>
          ) : (
            <span className="muted">no action chosen</span>
          )}
          {opp.contact_attempts > 0 && (
            <span className="muted">{opp.contact_attempts} contact attempt(s)</span>
          )}
        </span>
        {reason && <span className="queue-reason">{reason}</span>}
      </button>
    </li>
  );
}

export function RecoveryQueue({ opportunities, onSelectOpportunity }: RecoveryQueueProps) {
  const lanes = buildLanes(opportunities);
  const totalWaiting = lanes.reduce((n, lane) => n + lane.items.length, 0);

  if (totalWaiting === 0) {
    return (
      <section className="panel queue-panel">
        <div className="panel-header">
          <div>
            <h2>Recovery queue</h2>
            <span className="panel-sub">Nothing is waiting on an operator.</span>
          </div>
        </div>
        <p className="empty">
          Every unresolved opportunity is either progressing through the pipeline or has a
          recovery window with more than six hours left.
        </p>
      </section>
    );
  }

  return (
    <section className="panel queue-panel">
      <div className="panel-header">
        <div>
          <h2>Recovery queue</h2>
          <span className="panel-sub">
            What needs attention first, ranked by what acting is worth.
          </span>
        </div>
      </div>

      <div className="queue-lanes">
        {lanes.filter((lane) => lane.urgent || lane.items.length > 0).map((lane) => (
          <article
            key={lane.key}
            className={`queue-lane ${lane.urgent && lane.items.length > 0 ? "is-urgent" : ""}`}
          >
            <header className="queue-lane-head">
              <h3>{lane.title}</h3>
              <span className="queue-count">
                {lane.items.length} · {inr(laneValue(lane))}
              </span>
            </header>
            <p className="queue-rationale">{lane.rationale}</p>
            {lane.items.length === 0 ? (
              <p className="empty queue-empty">Nothing here.</p>
            ) : (
              <ul className="queue-list">
                {lane.items.slice(0, 5).map((opp) => (
                  <QueueRow key={opp.id} opp={opp} onSelect={onSelectOpportunity} />
                ))}
              </ul>
            )}
            {lane.items.length > 5 && (
              <p className="queue-more">
                and {lane.items.length - 5} more, worth{" "}
                {inr(lane.items.slice(5).reduce((t, o) => t + o.amount_minor, 0))}
              </p>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
