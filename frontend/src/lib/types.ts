// Response shapes of the CloudOpt API (backend/app/api/routes).

export type Role = "admin" | "viewer";

export interface User {
  id: number;
  email: string;
  full_name: string;
  role: Role;
  tenant_id: string | null;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface ReplayStatus {
  current_slot: number;
  sim_time: string;
  onboarded_at: string;
  trace_day: number;
  running: boolean;
  finished: boolean;
  seconds_per_slot: number;
  speedup: number;
  progress: number;
  scenario: Scenario | null;
  rl_available: boolean;
}

export interface Scenario {
  service_id: string;
  service_name: string;
  kind: "spike" | "drop" | "surge";
  magnitude: number;
  duration: number;
  slots_left: number;
}

export interface TrendPoint {
  time: string;
  cost: number;
  baseline_cost: number;
  avg_util: number;
  violation_rate: number;
  partial?: boolean;
}

export interface Overview {
  window_hours: number;
  services: number;
  tenants: number;
  hourly_run_rate: number;
  baseline_hourly_run_rate: number;
  projected_monthly_savings: number;
  savings_since_onboarding: number;
  savings_since_onboarding_pct: number | null;
  window_cost: number;
  prev_window_cost: number | null;
  sla_violation_rate: number | null;
  prev_sla_violation_rate: number | null;
  pre_onboarding_sla_violation_rate: number | null;
  avg_util: number | null;
  pre_onboarding_avg_util: number | null;
  pending_recommendations: number;
  anomalies: number;
  trend: TrendPoint[];
}

export interface TenantRow {
  id: string;
  name: string;
  pricing_key: string;
  sla_util: number;
  target_util: number;
  services: number;
  hourly_run_rate: number;
  baseline_hourly_run_rate: number;
  savings_pct: number | null;
  savings_since_onboarding: number;
  sla_violation_rate_24h: number | null;
  avg_util_24h: number | null;
  pending_recommendations: number;
  violations_by_hour: (number | null)[];
  hours_start: string;
}

export interface Tenant {
  id: string;
  name: string;
  pricing_key: string;
  sla_util: number;
  target_util: number;
}

export type ServiceMode = "recommend" | "autopilot" | "manual";

export interface Service {
  id: string;
  tenant_id: string;
  tenant_name?: string | null;
  name: string;
  trigger: string;
  replica_vcpu: number;
  replica_mem_gb: number;
  replicas: number;
  baseline_replicas: number;
  min_replicas: number;
  max_replicas: number;
  mode: ServiceMode;
  autopilot_max_step_pct: number;
  decision_engine: "forecast" | "rl";
  rps?: number | null;
  cpu_util?: number | null;
  cpu_util_peak?: number | null;
  sla_violation?: boolean | null;
}

export interface SeriesPoint {
  time: string;
  slot: number;
  demand: number;
  demand_mean: number;
  rps: number;
  replicas: number;
  capacity: number;
  cpu_util: number;
  cpu_util_peak: number;
  cost: number;
  baseline_cost: number;
  sla_violation: boolean;
  injected: boolean;
  forecast_p50: number | null;
  forecast_p10: number | null;
  forecast_p90: number | null;
}

export interface FuturePoint {
  time: string;
  horizon_minutes: number;
  p10: number;
  p50: number;
  p90: number;
  p95: number;
}

export interface Timeseries {
  service_id: string;
  onboarded_slot: number;
  sla_capacity_factor: number;
  points: SeriesPoint[];
  forecast: FuturePoint[];
}

export type RecStatus = "pending" | "approved" | "rejected" | "expired" | "auto_applied" | "superseded";

export interface RecommendationFacts {
  service_name: string;
  tenant_name: string;
  current_replicas: number;
  recommended_replicas: number;
  replica_size: string;
  observed_peak_now: number;
  forecast_p50_next_hour: number;
  forecast_p90_next_hour: number;
  horizon_minutes: number;
  predicted_util_if_unchanged: number;
  predicted_util_if_changed: number;
  target_util: number;
  sla_util: number;
  risk_current: number;
  risk_recommended: number;
  hourly_cost_delta: number;
  monthly_cost_delta: number;
  pricing_label: string;
  confidence: number;
  anomaly: { kind: string; observed: number; expected: number } | null;
}

export interface Recommendation {
  id: number;
  service_id: string;
  tenant_id: string;
  created_at: string;
  created_slot: number;
  expires_slot: number;
  action: "scale_out" | "scale_in";
  policy: string;
  current_replicas: number;
  recommended_replicas: number;
  hourly_cost_current: number;
  hourly_cost_recommended: number;
  risk_current: number;
  risk_recommended: number;
  confidence: number;
  facts: RecommendationFacts;
  explanation: string;
  explanation_source: "openai" | "openai_retry" | "fallback" | "template" | "pending";
  status: RecStatus;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
}

export interface Paged<T> {
  total: number;
  items: T[];
}

export interface Anomaly {
  id: number;
  service_id: string;
  tenant_id: string;
  time: string;
  kind: "spike" | "drop";
  score: number;
  observed: number;
  expected: number;
  expected_high: number;
  injected: boolean;
}

export interface Pricing {
  key: string;
  label: string;
  vcpu_hour: number;
  gb_hour: number;
  is_custom: boolean;
}

export interface AuditEntry {
  id: number;
  at: string;
  actor: string;
  action: string;
  target: string;
  details: Record<string, unknown>;
}

// Offline evaluation reports (reports/*.json)
export interface PolicyResult {
  policy: string;
  total_cost_usd: number;
  savings_vs_baseline_pct?: number;
  median_app_savings_pct?: number;
  violation_rate: number;
  apps_with_violation_share: number;
  mean_util: number;
  median_util: number;
  avg_replicas: number;
  scaling_actions_per_app_day: number;
}

export interface Evaluation {
  comparison: { meta: Record<string, unknown>; results: PolicyResult[] } | null;
  comparison_delay1: { meta: Record<string, unknown>; results: PolicyResult[] } | null;
  frontier: FrontierReport | null;
  forecast: Record<string, ForecastHorizonReport> | null;
  forecast_lstm: Record<string, unknown> | null;
  anomaly: AnomalyReport | null;
  rl: Record<string, unknown> | null;
  explainability: Record<string, unknown> | null;
}

export interface FrontierReport {
  meta: Record<string, unknown>;
  curves: { policy: string; label: string; points: { knob: number; cost: number; violation_rate: number }[] }[];
  headline?: Record<string, number | string>;
}

export interface ForecastMetrics {
  nmae: number;
  mae: number;
  rmse: number;
  mape_pct: number;
  wape_pct: number;
  n: number;
}

export interface ForecastHorizonReport {
  all_apps: Record<string, ForecastMetrics | number>;
  scaling_study_apps: Record<string, ForecastMetrics | number>;
  best_iteration: number;
  train_seconds: number;
}

export interface DetectorMetrics {
  precision: number;
  recall: number;
  f1: number;
  surge_false_alarm: number;
  alerts: number;
}

export interface AnomalyReport {
  method: string;
  test_events: Record<string, number>;
  residual_iforest: DetectorMetrics & { threshold: number };
  ablation_iforest_without_band_check: DetectorMetrics;
  ablation_forecast_band_only: DetectorMetrics;
  raw_threshold_baseline: DetectorMetrics & { k: number };
}
