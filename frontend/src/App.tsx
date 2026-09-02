import { useCallback, useEffect, useRef, useState } from "react";
import { fetchOpportunityQueue, fetchPolicyConfig, fetchRazorpayState, fetchSummary } from "./api/client";
import { connectStream } from "./api/stream";
import type { FeedItem, PolicyConfig, RazorpayState, Summary } from "./api/types";
import { Header, type TabKey } from "./components/Header";
import { KpiStrip } from "./components/KpiStrip";
import { LandingPage } from "./components/LandingPage";
import { MaintenanceBanner } from "./components/MaintenanceBanner";
import { OpportunityModal } from "./components/OpportunityModal";
import { GovernanceTab } from "./components/tabs/GovernanceTab";
import { IncidentsTab } from "./components/tabs/IncidentsTab";
import { OpportunitiesTab } from "./components/tabs/OpportunitiesTab";
import { OverviewTab } from "./components/tabs/OverviewTab";
import { RazorpayTab } from "./components/tabs/RazorpayTab";
import { SimulationTab } from "./components/tabs/SimulationTab";
import { Icon } from "./components/Icon";
import { inr } from "./utils/format";
import { getMaintenanceStatus } from "./utils/maintenanceWindow";

const POLL_MS = 8000;
const REFRESH_THROTTLE_MS = 1500;
const FEED_CAP = 250;
const pageCopy: Record<TabKey, { title: string; description: string }> = {
  overview: { title: "Live recovery feed", description: "Signals as payment states change, and what the engine did about them." },
  opportunities: { title: "Recovery queue", description: "Inspect decisions, safeguards, and verified outcomes." },
  razorpay: { title: "Razorpay state", description: "Reconciled lifecycle state from Razorpay webhooks and APIs." },
  incidents: { title: "Payment incidents", description: "Method and bank disruptions that need coordinated action." },
  simulation: { title: "Demo traffic", description: "Generate isolated synthetic scenarios without affecting live evidence." },
  governance: { title: "Evidence and policy", description: "Model eligibility, experiment quality, and execution guardrails." },
};

function TypewriterText({ text }: { text: string }) {
  const [reduceMotion] = useState(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const [displayed, setDisplayed] = useState(() => reduceMotion ? text : "");

  useEffect(() => {
    if (reduceMotion) return;

    let cursor = 0;
    const interval = window.setInterval(() => {
      cursor += 1;
      setDisplayed(text.slice(0, cursor));
      if (cursor >= text.length) window.clearInterval(interval);
    }, 18);

    return () => window.clearInterval(interval);
  }, [text, reduceMotion]);

  return <span className="typewriter-copy">{displayed}<span className="typewriter-caret" aria-hidden="true" /></span>;
}

export default function App() {
  const [entered, setEntered] = useState(false);
  const [activeTab, setActiveTab] = useState<TabKey>("overview");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [razorpayState, setRazorpayState] = useState<RazorpayState | null>(null);
  const [feed, setFeed] = useState<FeedItem[]>([]);
  const [connected, setConnected] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [selectedOpportunityId, setSelectedOpportunityId] = useState<string | null>(null);
  const [refreshSignal, setRefreshSignal] = useState(0);
  // Separate from refreshSignal (which ticks on every 8s poll): the ledger
  // count-up should only replay on mount or an explicit operator refresh,
  // never while someone is mid-read of a figure.
  const [manualRefreshKey, setManualRefreshKey] = useState(0);
  const lastRefresh = useRef(0);
  // Matches the failure-mix endpoint's own default: a demo install has only
  // seeded traffic, so the console opens showing it rather than a wall of
  // zeroes on a quiet day. The toggle still lets an operator switch to the
  // honest real-only view any time.
  const [includeSynthetic, setIncludeSynthetic] = useState(true);
  const [policy, setPolicy] = useState<PolicyConfig | null>(null);
  const [queueDepth, setQueueDepth] = useState<number | null>(null);
  const [maintenance, setMaintenance] = useState(() => getMaintenanceStatus());

  useEffect(() => {
    const check = () => setMaintenance(getMaintenanceStatus());
    const interval = setInterval(check, 60_000);
    return () => clearInterval(interval);
  }, []);

  const refresh = useCallback(async () => {
    if (getMaintenanceStatus().isDown) return;
    setIsRefreshing(true);
    const [summaryResult, stateResult] = await Promise.allSettled([
      fetchSummary(includeSynthetic),
      fetchRazorpayState(),
    ]);
    if (summaryResult.status === "fulfilled") setSummary(summaryResult.value);
    if (stateResult.status === "fulfilled") setRazorpayState(stateResult.value);
    const failures = [summaryResult, stateResult].filter((result) => result.status === "rejected");
    const failure = failures[0]?.reason;
    setRefreshError(failure instanceof Error ? failure.message : failure ? "Unable to refresh monitor data." : null);
    if (failures.length === 0) { setRefreshSignal((prev) => prev + 1); setLastUpdated(new Date()); }
    setIsRefreshing(false);
  }, [includeSynthetic]);

  useEffect(() => {
    if (maintenance.isDown) return;
    fetchPolicyConfig().then(setPolicy).catch(() => setPolicy(null));
  }, [maintenance.isDown]);

  useEffect(() => {
    if (maintenance.isDown) return;
    fetchOpportunityQueue(200)
      .then((rows) => setQueueDepth(rows.length))
      .catch(() => setQueueDepth(null));
  }, [refreshSignal, maintenance.isDown]);

  const manualRefresh = useCallback(() => {
    setManualRefreshKey((prev) => prev + 1);
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const initialRefresh = setTimeout(() => void refresh(), 0);
    const poll = setInterval(refresh, POLL_MS);
    return () => { clearTimeout(initialRefresh); clearInterval(poll); };
  }, [refresh]);

  useEffect(() => connectStream(
    (item) => { setConnected(true); setFeed((prev) => [item, ...prev].slice(0, FEED_CAP)); },
    () => {
      setConnected(true);
      const now = Date.now();
      if (now - lastRefresh.current > REFRESH_THROTTLE_MS) { lastRefresh.current = now; void refresh(); }
    },
    setConnected,
  ), [refresh]);

  const mode = razorpayState?.operating_mode ?? { razorpay_mode: "test", shadow_mode: true };
  const page = pageCopy[activeTab];
  const isMonitor = activeTab === "overview";
  const syncLabel = lastUpdated
    ? `Last sync ${lastUpdated.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}`
    : "Awaiting first sync";
  if (!entered) return <LandingPage onStart={() => setEntered(true)} />;

  return (
    <div className="app-container">
      <Header
        activeTab={activeTab}
        onSelectTab={setActiveTab}
        connected={connected}
        razorpayMode={mode.razorpay_mode}
        shadowMode={mode.shadow_mode}
        queueDepth={queueDepth}
        gateLabel={policy ? inr(policy.min_ev_margin_minor + 1500) : null}
        contactCap={policy?.max_contact_attempts ?? null}
      />
      <main className="main-content">
        {maintenance.isDown && <MaintenanceBanner message={maintenance.message} />}
        {/* Monitor keeps the full masthead; every inner page compresses to a
            single utility bar so the operator reaches data faster. */}
        {maintenance.isDown ? null : isMonitor ? (
          <>
            <div className="workspace-bar">
              <div className="breadcrumb"><span>Recovery operations</span><span className="breadcrumb-sep" aria-hidden="true" /><strong>{page.title}</strong></div>
              <div className={`stream-status ${connected ? "stream-status-live" : "stream-status-reconnecting"}`}>
                <span className="stream-status-dot" aria-hidden="true" />
                <span>{connected ? "Live stream" : "Reconnecting"}</span>
              </div>
            </div>
            <header className="page-header">
              <div className="page-heading"><span className="page-kicker">Operator workspace</span><h2>{page.title}</h2><p>{page.description}</p><div className="typewriter-line"><TypewriterText key={activeTab} text="Signals arrive here as payment states change." /></div></div>
              <div className="page-actions"><span className="last-updated">{syncLabel}</span><button type="button" className="btn btn-secondary" onClick={manualRefresh} disabled={isRefreshing}><Icon name="refresh" size={15} className={isRefreshing ? "is-spinning" : undefined} />{isRefreshing ? "Refreshing" : "Refresh data"}</button></div>
            </header>
          </>
        ) : (
          <header className="utility-bar">
            <div className="breadcrumb"><span>Recovery operations</span><span className="breadcrumb-sep" aria-hidden="true" /><strong>{page.title}</strong></div>
            <div className="utility-bar-actions">
              <span className={`stream-status ${connected ? "stream-status-live" : "stream-status-reconnecting"}`}>
                <span className="stream-status-dot" aria-hidden="true" />
                <span>{connected ? "Live" : "Reconnecting"}</span>
              </span>
              <span className="last-updated">{syncLabel}</span>
              <button type="button" className="btn btn-secondary btn-sm" onClick={manualRefresh} disabled={isRefreshing}><Icon name="refresh" size={14} className={isRefreshing ? "is-spinning" : undefined} />{isRefreshing ? "Refreshing" : "Refresh"}</button>
            </div>
          </header>
        )}
        {!maintenance.isDown && refreshError && <div className="app-alert" role="status"><div><strong>Data needs attention</strong><span className="app-alert-detail">{refreshError}</span></div><button type="button" className="btn btn-secondary btn-sm" onClick={manualRefresh} disabled={isRefreshing}>Try again</button></div>}
        {!maintenance.isDown && isMonitor && (
          <KpiStrip
            summary={summary}
            refreshKey={manualRefreshKey}
            includeSynthetic={includeSynthetic}
            onToggleSynthetic={setIncludeSynthetic}
          />
        )}
        <div className="tab-content-wrapper" hidden={maintenance.isDown}>
          {activeTab === "overview" && <OverviewTab summary={summary} feed={feed} onClearFeed={() => setFeed([])} onSelectOpportunity={setSelectedOpportunityId} />}
          {activeTab === "opportunities" && <OpportunitiesTab onSelectOpportunity={setSelectedOpportunityId} refreshSignal={refreshSignal} />}
          {activeTab === "razorpay" && <RazorpayTab state={razorpayState} onRefresh={refresh} />}
          {activeTab === "incidents" && <IncidentsTab onSelectOpportunity={setSelectedOpportunityId} refreshSignal={refreshSignal} />}
          {activeTab === "simulation" && <SimulationTab />}
          {activeTab === "governance" && <GovernanceTab />}
        </div>
      </main>
      {selectedOpportunityId && <OpportunityModal opportunityId={selectedOpportunityId} onClose={() => setSelectedOpportunityId(null)} onDecided={refresh} />}
    </div>
  );
}
