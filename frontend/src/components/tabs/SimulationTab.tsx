import { useEffect, useState } from "react";
import {
  fetchSimulationStatus,
  startSimulation,
  stopSimulation,
} from "../../api/client";
import type { SimulationConfig, SimulationStatus } from "../../api/types";
import { Icon } from "../Icon";

const PRESETS: Array<{
  name: string;
  desc: string;
  config: SimulationConfig;
}> = [
  {
    name: "HDFC UPI Degradation",
    desc: "Simulate a severe UPI bank gateway degradation on HDFC (70% failures).",
    config: {
      method: "upi",
      bank: "HDFC",
      failure_rate: 0.7,
      payments_per_minute: 90,
      amount_min_minor: 150_000,
      amount_max_minor: 4_000_000,
      duration_seconds: 60,
      subscription_share: 0.0,
      label: "HDFC UPI degradation",
    },
  },
  {
    name: "SBI Netbanking Outage",
    desc: "Simulate an abrupt major outage on SBI Netbanking (85% timeout failures).",
    config: {
      method: "netbanking",
      bank: "SBI",
      failure_rate: 0.85,
      payments_per_minute: 60,
      amount_min_minor: 500_000,
      amount_max_minor: 5_000_000,
      duration_seconds: 60,
      subscription_share: 0.0,
      label: "SBI Netbanking outage",
    },
  },
  {
    name: "Card Gateway Timeout",
    desc: "Simulate intermittent authorization network timeouts on Card payments.",
    config: {
      method: "card",
      bank: "ICICI",
      failure_rate: 0.5,
      payments_per_minute: 120,
      amount_min_minor: 100_000,
      amount_max_minor: 2_500_000,
      duration_seconds: 45,
      subscription_share: 0.0,
      label: "ICICI Card network latency",
    },
  },
  {
    name: "SaaS Subscription Renewal Failures",
    desc: "Recurring invoice renewal wave with high card renewal friction (60% sub share).",
    config: {
      method: "card",
      bank: "AXIS",
      failure_rate: 0.45,
      payments_per_minute: 80,
      amount_min_minor: 200_000,
      amount_max_minor: 1_500_000,
      duration_seconds: 60,
      subscription_share: 0.6,
      label: "SaaS Subscription Renewal Wave",
    },
  },
];

export function SimulationTab() {
  const [config, setConfig] = useState<SimulationConfig>({
    method: "upi",
    bank: "HDFC",
    failure_rate: 0.65,
    payments_per_minute: 90,
    amount_min_minor: 150_000,
    amount_max_minor: 3_500_000,
    duration_seconds: 60,
    subscription_share: 0.0,
    label: "Custom merchant failure simulation",
  });

  const [status, setStatus] = useState<SimulationStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadStatus();
    const interval = setInterval(loadStatus, 2000);
    return () => clearInterval(interval);
  }, []);

  async function loadStatus() {
    try {
      const res = await fetchSimulationStatus();
      setStatus(res);
    } catch {
      /* ignore background poll */
    }
  }

  async function handleStart() {
    setLoading(true);
    setError(null);
    try {
      const result = await startSimulation(config);
      if (result.started === false) {
        throw new Error(String(result.reason ?? "Simulation was not started"));
      }
      await loadStatus();
    } catch (err: any) {
      setError(err.message || "Failed to start simulation");
    } finally {
      setLoading(false);
    }
  }

  async function handleStop() {
    setLoading(true);
    setError(null);
    try {
      await stopSimulation();
      await loadStatus();
    } catch (err: any) {
      setError(err.message || "Failed to stop simulation");
    } finally {
      setLoading(false);
    }
  }

  function applyPreset(preset: (typeof PRESETS)[number]) {
    setConfig({ ...preset.config });
  }

  const isRunning = Boolean(status?.active);
  const amountRangeInvalid = config.amount_min_minor > config.amount_max_minor;

  return (
    <div className="simulation-layout">
      {/* Left Column: Preset Scenarios & Config Form */}
      <div className="sim-config-column">
        {/* Preset Scenarios */}
        <section className="panel">
          <div className="panel-header">
            <h3>Preset Incident Scenarios</h3>
            <span className="panel-sub">Select a pre-tuned failure scenario</span>
          </div>

          <div className="preset-grid">
            {PRESETS.map((p) => (
              <button
                key={p.name}
                type="button"
                className="preset-card"
                onClick={() => applyPreset(p)}
                disabled={isRunning}
              >
                <div className="preset-title">{p.name}</div>
                <div className="preset-desc">{p.desc}</div>
                <div className="preset-meta">
                  <span>{p.config.bank}</span> ·{" "}
                  <span>{p.config.method.toUpperCase()}</span> ·{" "}
                  <span>{(p.config.failure_rate * 100).toFixed(0)}% fail</span>
                </div>
              </button>
            ))}
          </div>
        </section>

        {/* Custom Simulation Controls Form */}
        <section className="panel">
          <div className="panel-header">
            <h3>Simulation Configuration Parameters</h3>
            <span className="panel-sub">Tune synthetic volume, failure rates, and amounts</span>
          </div>

          {error && <div className="alert-box alert-error">{error}</div>}
          {amountRangeInvalid && (
            <div className="alert-box alert-error">Minimum amount cannot exceed maximum amount.</div>
          )}
          {status?.error && <div className="alert-box alert-error">Simulation failed: {status.error}</div>}

          <div className="form-grid">
            <div className="form-group">
              <label>Payment Method</label>
              <select
                className="form-control"
                value={config.method}
                disabled={isRunning}
                onChange={(e) => setConfig({ ...config, method: e.target.value })}
              >
                <option value="upi">UPI</option>
                <option value="card">Card</option>
                <option value="netbanking">Netbanking</option>
                <option value="wallet">Wallet</option>
              </select>
            </div>

            <div className="form-group">
              <label>Target Bank</label>
              <input
                type="text"
                className="form-control"
                value={config.bank}
                disabled={isRunning}
                onChange={(e) => setConfig({ ...config, bank: e.target.value })}
                placeholder="e.g. HDFC, SBI, ICICI"
              />
            </div>

            <div className="form-group span-2">
              <div className="slider-label-row">
                <label>Failure Rate</label>
                <span className="slider-val">{(config.failure_rate * 100).toFixed(0)}%</span>
              </div>
              <input
                type="range"
                min="0"
                max="0.95"
                step="0.05"
                className="form-slider"
                value={config.failure_rate}
                disabled={isRunning}
                onChange={(e) =>
                  setConfig({ ...config, failure_rate: parseFloat(e.target.value) })
                }
              />
            </div>

            <div className="form-group">
              <div className="slider-label-row">
                <label>Payments / Minute</label>
                <span className="slider-val">{config.payments_per_minute}/min</span>
              </div>
              <input
                type="range"
                min="10"
                max="300"
                step="10"
                className="form-slider"
                value={config.payments_per_minute}
                disabled={isRunning}
                onChange={(e) =>
                  setConfig({ ...config, payments_per_minute: parseInt(e.target.value, 10) })
                }
              />
            </div>

            <div className="form-group">
              <div className="slider-label-row">
                <label>Duration (Seconds)</label>
                <span className="slider-val">{config.duration_seconds}s</span>
              </div>
              <input
                type="range"
                min="30"
                max="300"
                step="15"
                className="form-slider"
                value={config.duration_seconds}
                disabled={isRunning}
                onChange={(e) =>
                  setConfig({ ...config, duration_seconds: parseInt(e.target.value, 10) })
                }
              />
            </div>

            <div className="form-group">
              <label>Min Amount (₹)</label>
              <input
                type="number"
                className="form-control"
                value={config.amount_min_minor / 100}
                disabled={isRunning}
                onChange={(e) =>
                  setConfig({
                    ...config,
                    amount_min_minor: Math.max(100, parseInt(e.target.value || "0", 10) * 100),
                  })
                }
              />
            </div>

            <div className="form-group">
              <label>Max Amount (₹)</label>
              <input
                type="number"
                className="form-control"
                value={config.amount_max_minor / 100}
                disabled={isRunning}
                onChange={(e) =>
                  setConfig({
                    ...config,
                    amount_max_minor: Math.max(100, parseInt(e.target.value || "0", 10) * 100),
                  })
                }
              />
            </div>

            <div className="form-group span-2">
              <div className="slider-label-row">
                <label>Subscription Share</label>
                <span className="slider-val">
                  {((config.subscription_share || 0) * 100).toFixed(0)}%
                </span>
              </div>
              <input
                type="range"
                min="0"
                max="0.8"
                step="0.1"
                className="form-slider"
                value={config.subscription_share || 0}
                disabled={isRunning}
                onChange={(e) =>
                  setConfig({
                    ...config,
                    subscription_share: parseFloat(e.target.value),
                  })
                }
              />
            </div>

            <div className="form-group span-2">
              <label>Simulation Run Label</label>
              <input
                type="text"
                className="form-control"
                value={config.label}
                disabled={isRunning}
                onChange={(e) => setConfig({ ...config, label: e.target.value })}
                placeholder="Scenario description for audit trail"
              />
            </div>
          </div>

          <div className="form-actions">
            {!isRunning ? (
              <button
                type="button"
                className="btn btn-primary btn-lg"
                onClick={handleStart}
                disabled={loading || amountRangeInvalid}
              >
                <Icon name="play" size={15} />{loading ? "Starting simulation" : "Start failure simulation"}
              </button>
            ) : (
              <button
                type="button"
                className="btn btn-danger btn-lg"
                onClick={handleStop}
                disabled={loading}
              >
                <Icon name="stop" size={15} />{loading ? "Stopping" : "Stop simulation"}
              </button>
            )}
          </div>
        </section>
      </div>

      {/* Right Column: Live Simulation Telemetry Monitor */}
      <div className="sim-monitor-column">
        <section className={`panel sim-telemetry-panel ${isRunning ? "telemetry-active" : ""}`}>
          <div className="panel-header">
            <div>
              <h3>Simulation Telemetry Monitor</h3>
              <span className="panel-sub">Real-time synthetic stream injection status</span>
            </div>
            <span
              className={`status-pill ${
                isRunning ? "st-running" : status?.status === "stopped" ? "st-stopped" : "st-idle"
              }`}
            >
              {isRunning ? "RUNNING" : status?.status?.toUpperCase() ?? "IDLE"}
            </span>
          </div>

          <div className="telemetry-grid">
            <div className="telemetry-card">
              <span className="telemetry-lbl">Run Status</span>
              <div className="telemetry-val">
                {isRunning ? (
                  <span className="pulsing-text">Active Stream</span>
                ) : (
                  <span>Inactive</span>
                )}
              </div>
              <span className="telemetry-sub">ID: {status?.run_id ?? "—"}</span>
            </div>

            <div className="telemetry-card">
              <span className="telemetry-lbl">Elapsed Time</span>
              <div className="telemetry-val">
                {status?.elapsed_seconds !== undefined ? `${status.elapsed_seconds}s` : "0s"}
              </div>
              <span className="telemetry-sub">
                Target: {status?.config?.duration_seconds ?? config.duration_seconds}s
              </span>
            </div>

            <div className="telemetry-card">
              <span className="telemetry-lbl">Payments Generated</span>
              <div className="telemetry-val good-val">{status?.generated_payments ?? 0}</div>
              <span className="telemetry-sub">Synthetic transactions</span>
            </div>

            <div className="telemetry-card">
              <span className="telemetry-lbl">Failures Injected</span>
              <div className="telemetry-val risk-val">{status?.generated_failures ?? 0}</div>
              <span className="telemetry-sub">
                {status?.generated_payments && status?.generated_payments > 0
                  ? `${(((status.generated_failures ?? 0) / status.generated_payments) * 100).toFixed(0)}% actual fail`
                  : "Awaiting generation"}
              </span>
            </div>
          </div>

          {isRunning && (
            <div className="alert-box alert-warn sim-active-alert">
              <strong className="inline-icon-text"><span className="live-marker" aria-hidden="true" />Synthetic degradation in progress</strong>
              <p>
                Payments are being processed through the real event pipeline. Check the{" "}
                <strong>Live Event Stream</strong> to see incoming failures, and navigate to{" "}
                <strong>Incidents & Outages</strong> to watch the Anomaly Detection Agent detect the failure spike in real-time.
              </p>
            </div>
          )}

          <div className="sim-info-box">
            <h4>How Simulation Interacts with Live Recovery</h4>
            <p>
              The simulation engine injects cleanly labeled synthetic transactions into the processing boundary. The Detection Agent computes EWMA, CUSUM, and sliding window excess rates to automatically identify anomalies and trigger batch automated interventions under the configured safety budgets.
            </p>
          </div>
        </section>
      </div>
    </div>
  );
}
