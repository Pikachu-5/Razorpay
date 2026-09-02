export interface Summary {
  revenue_at_risk_minor: number;
  open_opportunities: number;
  recovered_today_minor: number;
  recovered_natural_today_minor: number;
  events_received_today: number;
  payments_by_status: Record<string, number>;
  success_rate_today: number | null;
  revenue_adjustments_today_minor: number;
  net_recovered_today_minor: number;
  synthetic_payments_excluded: number;
}

export interface RazorpayStateItem {
  id?: string;
  external_id?: string;
  status: string;
  source: string;
  amount_minor?: number;
  amount_paid_minor?: number;
  amount_due_minor?: number;
  method?: string | null;
  severity?: string | null;
  instrument?: string | null;
  plan_id?: string | null;
  subscription_id?: string | null;
  payment_id?: string | null;
  kind?: string;
  attempts?: number;
  paid_count?: number;
  remaining_count?: number;
  short_url?: string | null;
}

export interface RazorpayState {
  operating_mode: { razorpay_mode: string; shadow_mode: boolean };
  orders: RazorpayStateItem[];
  payment_links: RazorpayStateItem[];
  downtimes: RazorpayStateItem[];
  subscriptions: RazorpayStateItem[];
  invoices: RazorpayStateItem[];
  revenue_adjustments: RazorpayStateItem[];
}

export interface ReconciliationResult {
  status: string;
  checked: Record<string, number>;
  updated: Record<string, number>;
  errors: string[];
}

export interface StreamEnvelope {
  kind: string;
  ts: string;
  data: Record<string, unknown>;
}

export interface FeedItem {
  id: string;
  time: string;
  kind: string;
  headline: string;
  detail: string;
  rawData?: Record<string, unknown>;
}

export interface Opportunity {
  id: string;
  status: string;
  category: string;
  amount_minor: number;
  experiment_group: string;
  contact_attempts: number;
  best_action: string | null;
  expected_recovery_minor: number | null;
  window_ends_at: string;
  created_at: string;
  closed_reason?: string | null;
  source?: string;
  is_synthetic?: boolean;
  simulation_run_id?: string | null;
  assignment_probability?: number | null;
}

export interface PolicyRuleResult {
  rule: string;
  passed: boolean;
  detail: string;
}

export interface PolicyDecision {
  allowed: boolean;
  action: string;
  rules: PolicyRuleResult[];
}

export interface DecisionAudit {
  id: string;
  trigger: string;
  diagnosis: {
    classification: string;
    summary: string;
    confidence: number;
    evidence?: string[];
  } | null;
  model_version: string | null;
  predictions: Record<
    string,
    {
      action: string;
      probability: number | null;
      cost_minor: number;
      expected_recovery_minor: number;
    }
  > | null;
  recommended_action: string | null;
  expected_recovery_minor: number | null;
  policy_decision: PolicyDecision | null;
  executed_action: string | null;
  execution_result: Record<string, unknown> | null;
  verified_outcome: string | null;
  recovered_amount_minor: number | null;
  created_at: string;
}

export interface OpportunityDetail {
  opportunity: Opportunity;
  payment: {
    razorpay_payment_id: string | null;
    method: string | null;
    bank: string | null;
    error_reason: string | null;
    error_description: string | null;
    occurred_at: string | null;
  } | null;
  customer: {
    identity_key: string | null;
    email: string | null;
  } | null;
  decisions: DecisionAudit[];
  interventions: Array<{
    action: string;
    status: string;
    reference: string | null;
    payload: Record<string, unknown> | null;
    created_at: string;
  }>;
}

export interface Incident {
  id: string;
  status: string;
  title: string;
  severity: string;
  method: string | null;
  bank: string | null;
  revenue_at_risk_minor: number;
  affected_failures: number;
  interventions_executed: number;
  intervention_budget: number;
  source: string;
  started_at: string;
  resolved_at: string | null;
  detection_stats?: Record<string, unknown> | null;
  affected_opportunities?: Array<{
    id: string;
    status: string;
    amount_minor: number;
    best_action: string | null;
    expected_recovery_minor: number | null;
  }>;
}

export interface SimulationConfig {
  method: string;
  bank: string;
  failure_rate: number;
  payments_per_minute: number;
  amount_min_minor: number;
  amount_max_minor: number;
  duration_seconds: number;
  subscription_share?: number;
  label?: string;
  recovery_rate_treatment?: number;
  recovery_rate_control?: number;
}

export interface SimulationStatus {
  active: boolean;
  run_id?: string;
  status?: string;
  label?: string;
  config?: SimulationConfig;
  elapsed_seconds?: number;
  generated_payments?: number;
  generated_failures?: number;
  simulated_recoveries?: number;
  synthetic?: boolean;
  error?: string | null;
}

export interface ActionMetrics {
  n: number;
  positive_rate: number;
  roc_auc: number;
  log_loss: number;
  brier: number;
  mean_predicted: number;
}

export interface ModelCard {
  version: string;
  artifact?: string;
  model_type: string;
  trained_at?: string;
  data_dir?: string;
  rows_train_val_test?: [number, number, number];
  per_action?: {
    validation?: Record<string, ActionMetrics>;
    test?: Record<string, ActionMetrics>;
  };
  economics_test?: EconomicsBenchmark;
  natural_recovery_baseline?: Record<string, number>;
  natural_recovery_baseline_source?: string;
  note?: string;
  data_provenance?: string;
  deployment_tier?: string;
  action_quality?: Record<string, { enabled: boolean; reasons: string[]; metrics?: ActionMetrics }>;
}

/** One selection policy scored on the held-out replay, at a fixed budget. */
export interface EconomicsArm {
  recovered_minor: number;
  expected_natural_recovery_minor: number;
  incremental_recovery_minor: number;
  intervention_cost_minor: number;
  net_incremental_minor: number;
  selection: string;
}

export interface EconomicsBenchmark {
  opportunities_scored: number;
  universe?: number;
  budget_fraction: number;
  budget_k?: number;
  objective?: string;
  arms?: {
    model_policy: EconomicsArm;
    value_ranked_link: EconomicsArm;
    random_link: EconomicsArm;
  };
  /** Net incremental lift against the strongest policy that uses no model. */
  lift_pct: number;
  lift_vs_value_ranked_pct?: number;
  lift_vs_random_pct?: number;
  unobservable_choices?: number;
  caveats?: string[];
}

/** One failure class: how much of the book it is, and how it resolves untouched. */
export interface FailureClass {
  group: string;
  label: string;
  play: string;
  count: number;
  value_minor: number;
  share_of_count: number;
  share_of_value: number;
  recovered_count: number;
  recovery_rate: number;
  industry_reference?: string | null;
}

export interface FailureMix {
  classes: FailureClass[];
  total_count: number;
  total_value_minor: number;
  includes_simulated_traffic: boolean;
}

export interface GroupExperimentStats {
  total_opportunities: number;
  recovered_count: number;
  recovered_amount_minor: number;
  conversion_rate: number;
}

export interface ExperimentMetrics {
  treatment: GroupExperimentStats;
  control: GroupExperimentStats;
  causal_lift_pct: number;
  delta_conversion_rate: number;
  incremental_revenue_minor: number;
  incremental_revenue_inr: number;
  z_score: number;
  p_value: number;
  statistically_significant: boolean;
  synthetic_excluded?: boolean;
  source_counts?: Record<string, number>;
  minimum_sample_met?: boolean;
  includes_simulated_traffic?: boolean;
  evidence_quality?: string;
}

export interface ModelComparison {
  active_version: string;
  champion: ModelCard | null;
  challengers: ModelCard[];
  all_cards: ModelCard[];
}

export interface MlStatus {
  model_source: string;
  model_version: string;
  actions_trained: string[];
  promoted_pointer: boolean;
  artifacts_dir: string;
  actions_enabled?: string[];
  /** Trained but quarantined: still decidable, served by the heuristic. */
  actions_heuristic_fallback?: string[];
  natural_recovery_baseline?: Record<string, number>;
  natural_recovery_baseline_source?: string;
  data_provenance?: string;
}


export interface PolicyConfig {
  kill_switch: boolean;
  max_amount_minor: number;
  max_amount_inr: number;
  max_contact_attempts: number;
  cooldown_minutes: number;
  confidence_floor: number;
  min_ev_margin_minor: number;
  min_ev_margin_inr: number;
}

export const ALL_EVENT_KINDS = [
  "razorpay_event",
  "payment_recorded",
  "payment_updated",
  "opportunity_created",
  "opportunity_expired",
  "opportunity_resolved",
  "diagnosis_completed",
  "prediction_completed",
  "policy_evaluated",
  "decision_finalized",
  "decision_failed",
  "incident_detected",
  "incident_response",
  "incident_resolved",
  "verification_completed",
] as const;

export type EventKind = (typeof ALL_EVENT_KINDS)[number];

export interface OperatingMode {
  shadow_mode: boolean;
  razorpay_mode: string;
  razorpay_configured: boolean;
  simulate_interventions: boolean;
  customer_side_effects_enabled: boolean;
  /** False when operator actions on this install need no key at all. */
  control_plane_authenticated?: boolean;
  /** A declared public demo: unauthenticated on purpose, and badged as such. */
  control_plane_open_demo?: boolean;
  /** "process" while the shadow-mode override is not shared between workers. */
  shadow_mode_scope?: string;
}
