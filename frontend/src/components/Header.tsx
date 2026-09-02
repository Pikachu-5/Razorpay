import { useState } from "react";
import { ConfirmAction } from "./ConfirmAction";
import { Icon } from "./Icon";

export type TabKey = "overview" | "opportunities" | "razorpay" | "incidents" | "simulation" | "governance";

/**
 * The one brand mark: a pulse line, the trace a payment leaves behind.
 * Used here in the nav only — never repeated through content.
 */
function PulseMark({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={2.75} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 12h4l2.2-7 4.1 14L16 9l1.8 3H21" />
    </svg>
  );
}

interface HeaderProps {
  activeTab: TabKey;
  onSelectTab: (tab: TabKey) => void;
  connected: boolean;
  razorpayMode: string;
  shadowMode: boolean;
  /** A declared public demo where operator actions need no key. Said plainly. */
  openDemo?: boolean;
  /** Unresolved work, shown as the agent's current load. */
  queueDepth?: number | null;
  /** The expected-value floor every automated action has to clear. */
  gateLabel?: string | null;
  contactCap?: number | null;
  onOpenTour: () => void;
  /** Flips shadow mode. Consequential in the live direction, so callers get a preflight confirm. */
  onToggleExecution: () => Promise<void>;
}

const tabs: Array<{ key: TabKey; label: string; hint: string }> = [
  // "Live feed" over the older "Monitor": it names what the screen actually
  // shows rather than the act of watching it.
  { key: "overview", label: "Live feed", hint: "Signals as they arrive" },
  { key: "opportunities", label: "Recovery queue", hint: "Decisions and outcomes" },
  { key: "razorpay", label: "Razorpay state", hint: "Orders and lifecycles" },
  { key: "incidents", label: "Incidents", hint: "Payment disruptions" },
  { key: "simulation", label: "Demo traffic", hint: "Synthetic, isolated" },
  { key: "governance", label: "Evidence", hint: "Models and policy" },
];

export function Header({
  activeTab, onSelectTab, connected, razorpayMode, shadowMode, openDemo = false,
  queueDepth = null, gateLabel = null, contactCap = null, onOpenTour, onToggleExecution,
}: HeaderProps) {
  const [confirmingExecution, setConfirmingExecution] = useState(false);
  const [switchingExecution, setSwitchingExecution] = useState(false);

  async function confirmToggle() {
    setSwitchingExecution(true);
    try {
      await onToggleExecution();
      setConfirmingExecution(false);
    } finally {
      setSwitchingExecution(false);
    }
  }

  return (
    <aside className="sidebar">
      <div className="brand-group">
        <div className="brand-badge" aria-hidden="true"><PulseMark size={17} /></div>
        <div><h1>Recover</h1><span className="subtitle">Revenue control plane</span></div>
        <button type="button" className="tour-reopen" onClick={onOpenTour} aria-label="Take the guided tour" title="Take the guided tour">
          <Icon name="info" size={16} />
        </button>
      </div>

      <nav className="nav-tabs" aria-label="Primary navigation">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            className={`nav-tab ${activeTab === tab.key ? "nav-tab-active" : ""}`}
            onClick={() => onSelectTab(tab.key)}
            aria-current={activeTab === tab.key ? "page" : undefined}
          >
            <span className="nav-copy"><span>{tab.label}</span><small>{tab.hint}</small></span>
          </button>
        ))}
      </nav>

      <div className="sidebar-status">
        <div className="agent-state">
          <div className="agent-state-k">Agent state</div>
          <div className="agent-state-v">
            {shadowMode ? "Observing" : "Acting"}
            {queueDepth === null ? "" : ` · ${queueDepth} in queue`}
          </div>
          <div className="agent-state-bar" aria-hidden="true"><span /></div>
          <div className="agent-state-m">
            {gateLabel ? `EV gate ${gateLabel}` : "EV gate —"}
            {contactCap ? ` · ${contactCap}-contact cap` : ""}
          </div>
        </div>

        <div className="mode-row"><span>Environment</span><strong>Razorpay {razorpayMode}</strong></div>
        {openDemo && (
          <div className="mode-row mode-row-openaccess">
            <span>Access</span>
            <strong title="Operator actions on this install need no key. Forced model promotion still does.">
              Open demo · no key
            </strong>
          </div>
        )}
        <div className="mode-row">
          <span>Execution</span>
          <strong>{shadowMode ? "Observe only" : "Acting"}</strong>
          <button type="button" className="mode-switch" onClick={() => setConfirmingExecution(true)}>
            {shadowMode ? "Arm live" : "Return to observe-only"}
          </button>
        </div>
        <div className="mode-row mode-row-connection">
          <span className={`dot ${connected ? "dot-on" : "dot-off"}`} aria-hidden="true"><i /><i /><i /></span>
          <span>{connected ? "Stream connected" : "Reconnecting"}</span>
        </div>
      </div>

      {confirmingExecution && (
        <ConfirmAction
          title={shadowMode ? "Arm live execution?" : "Return to observe-only?"}
          summary={
            shadowMode
              ? "The agent will start sending real payment links, reminders, and instrument-swap requests to customers."
              : "The agent will go back to scoring and recording decisions without contacting any customer."
          }
          facts={[
            { label: "Environment", value: `Razorpay ${razorpayMode}` },
            {
              label: "New execution mode",
              value: shadowMode ? "LIVE — customers can be contacted" : "Observe only — nothing is sent",
              emphasis: true,
            },
          ]}
          confirmLabel={shadowMode ? "Arm live execution" : "Return to observe-only"}
          danger={shadowMode}
          safeNote={shadowMode ? null : "Nothing further is sent once this takes effect."}
          busy={switchingExecution}
          onConfirm={confirmToggle}
          onCancel={() => setConfirmingExecution(false)}
        />
      )}
    </aside>
  );
}
