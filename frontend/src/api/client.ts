import axios from 'axios'

const api = axios.create({ baseURL: '/api/v1' })
const proxy = axios.create({ baseURL: '/v1' })

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

proxy.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401) {
      localStorage.removeItem('token')
      window.location.href = '/login'
    }
    return Promise.reject(err)
  }
)

export default api

// Typed helpers
export const authApi = {
  login: (password: string) =>
    api.post<{ token: string }>('/auth/login', { password }).then((r) => r.data),
}

export const poolApi = {
  summary: () =>
    api.get<PoolSummary>('/pool/summary').then((r) => r.data),
}

export const channelsApi = {
  list: () => api.get<Channel[]>('/channels').then((r) => r.data),
  providers: () => api.get<Provider[]>('/channels/providers').then((r) => r.data),
  create: (data: CreateChannelInput) =>
    api.post<Channel>('/channels', data).then((r) => r.data),
  update: (id: string, data: Partial<Channel>) =>
    api.patch<Channel>(`/channels/${id}`, data).then((r) => r.data),
  delete: (id: string) => api.delete(`/channels/${id}`),
  probe: (id: string) => api.post(`/channels/${id}/probe`),
}

export const modelsApi = {
  list: (params?: ModelListParams, signal?: AbortSignal) =>
    api.get<ModelRow[]>('/models', { params, signal }).then((r) => r.data),
  get: (id: string) => api.get<ModelRow>(`/models/${id}`).then((r) => r.data),
  healthHistory: (id: string, period: '24h' | '7d' = '24h') =>
    api.get<HealthRecord[]>(`/models/${id}/health-history`, { params: { period } }).then((r) => r.data),
}

export const settingsApi = {
  get: () => api.get<Settings>('/settings').then((r) => r.data),
  update: (data: Partial<Settings>) => api.patch('/settings', data),
}

export const apiKeysApi = {
  list: () => api.get<ApiKeyRow[]>('/apikeys').then((r) => r.data),
  create: (data: ApiKeyCreateInput) => api.post<ApiKeyCreated>('/apikeys', data).then((r) => r.data),
  update: (id: string, data: Partial<ApiKeyCreateInput> & { is_active?: boolean }) =>
    api.patch(`/apikeys/${id}`, data),
  delete: (id: string) => api.delete(`/apikeys/${id}`),
}

export const acApi = {
  status: () => proxy.get<AcStatus>('/ac/status').then((r) => r.data),
  models: () => proxy.get<AcModelsResponse>('/ac/models').then((r) => r.data),
  selfTest: (model = 'auto:text') => proxy.post<AcSelfTest>('/ac/self-test', { model }).then((r) => r.data),
}

export const candidatesApi = {
  list: (includeConfigured = false) =>
    api.get<CandidateProvider[]>('/candidates', { params: { include_configured: includeConfigured } }).then((r) => r.data),
  sources: () => api.get<CandidateSourceState[]>('/candidates/sources').then((r) => r.data),
  refresh: () => api.post<CandidateRefreshResult>('/candidates/refresh').then((r) => r.data),
  yamlDraft: (id: string) => api.get<{ provider_id: string; yaml: string }>(`/candidates/${id}/yaml-draft`).then((r) => r.data),
  review: (id: string, status: 'pending' | 'reviewed' | 'ignored') =>
    api.patch<CandidateProvider>(`/candidates/${id}`, { status }).then((r) => r.data),
}

export const notificationsApi = {
  list: (includeResolved = false) =>
    api.get<NotificationRow[]>('/notifications', { params: { include_resolved: includeResolved } }).then((r) => r.data),
  unreadCount: () => api.get<{ count: number }>('/notifications/unread-count').then((r) => r.data),
  update: (id: string, status: 'read' | 'dismissed') =>
    api.patch<NotificationRow>(`/notifications/${id}`, { status }).then((r) => r.data),
  readAll: () => api.post<{ updated: number }>('/notifications/read-all').then((r) => r.data),
}

// Types
export interface PoolSummary {
  total_channels: number
  enabled_channels: number
  free_model_count: number
  available_model_count: number
  health_distribution: Record<string, number>
  invalid_key_count: number
  pending_candidate_count: number
  pending_policy_change_count: number
  recheck_count_24h: number
  unread_notification_count: number
}

export interface NotificationRow {
  id: string
  dedupe_key: string
  category: 'channel' | 'candidate' | 'policy_change' | 'candidate_source'
  severity: 'critical' | 'warning' | 'info'
  title: string
  message: string
  action_path: string | null
  payload: Record<string, unknown>
  status: 'unread' | 'read' | 'dismissed'
  created_at: string
  updated_at: string
  read_at: string | null
  resolved_at: string | null
}

export interface CandidateProvider {
  provider_id: string
  name: string
  homepage_url: string
  base_url: string | null
  summary: string
  compatibility: 'openai_compatible' | 'special_or_unknown'
  access_type: 'unknown' | 'permanent_free' | 'recurring_free' | 'quota_limited' | 'trial_credit' | 'credit_metered' | 'card_required'
  requires_card: boolean
  admission_status: 'review_required' | 'excluded'
  exclusion_reason: string | null
  model_count: number
  models: string[]
  sources: string[]
  status: 'pending' | 'reviewed' | 'ignored' | 'configured'
  has_yaml_draft: boolean
  first_seen_at: string
  last_seen_at: string
  last_changed_at: string
}

export interface CandidateSourceState {
  source_id: string
  url: string
  consecutive_failures: number
  last_attempt_at: string | null
  last_success_at: string | null
  last_error: string | null
  last_candidate_count: number
  needs_attention: boolean
}

export interface CandidateRefreshResult {
  successes: Record<string, number>
  failures: Record<string, string>
}

export interface Channel {
  id: string
  provider_type: string
  name: string
  api_key_hint: string
  base_url: string | null
  enabled: boolean
  created_at: string
  last_probed_at: string | null
  status: 'active' | 'key_invalid' | 'key_expired' | 'unconfigured' | 'suspended'
  status_reason: string | null
  status_changed_at: string
  key_expires_at: string | null
  config_type: 'custom_adapter' | 'declarative'
  discovery_source: string
  compliance_note: string
  free_model_count: number
}

export interface Provider {
  id: string
  name: string
  base_url: string
  config_type: 'custom' | 'declarative'
  requirements?: {
    requires_card: boolean
    requires_phone: boolean
    requires_realname: boolean
  }
  setup?: {
    description: string
    key_hint: string
    console_url: string
    key_optional?: boolean
  }
  compliance?: {
    risk: 'low' | 'medium' | 'high' | 'unknown'
    note: string
    reviewed_at: string
    sources: string[]
  }
}

export interface CreateChannelInput {
  provider_type: string
  name?: string
  api_key: string
  base_url?: string
}

export interface ModelRow {
  id: string
  channel_id: string
  model_id: string
  display_name: string | null
  category: string | null
  context_length: number | null
  rate_limit: string | null
  rate_limit_source: string | null       // manual / observed
  rate_limit_updated_at: string | null
  is_free: boolean | null
  free_type: string | null
  free_source: string | null
  health_status: string
  last_response_ms: number | null
  last_checked_at: string | null
  last_success_at: string | null
  last_verified_at: string | null
  verification_method: string | null
  staleness_threshold_days: number
  free_expires_at: string | null
  rate_limited_until: string | null
  last_429_at: string | null
  consecutive_429: number
  provider_type: string | null
  provider_name: string | null
  base_url: string | null
  param_size: number | null
}

export interface ModelListParams {
  provider?: string
  category?: string
  free_only?: boolean
  healthy_only?: boolean
  q?: string
  sort_by?: 'smart' | 'fast'
}

export interface HealthRecord {
  id: number
  model_id: string
  checked_at: string
  status: string
  response_ms: number | null
  error_code: string | null
  is_passive: boolean
  verification_method: string | null
  http_status: number | null
  check_run_id: string | null
  failure_reason: string | null
  rate_limit_snapshot: string | null
}

export interface Settings {
  discovery_interval_hours: string
  probe_interval_hours: string
  slow_threshold_ms: string
  whitelist_version: string
}

export interface ApiKeyRow {
  id: string
  name: string
  key: string
  key_prefix: string
  is_active: boolean
  created_at: string
  last_used_at: string | null
  provider_whitelist: string[]
  provider_blacklist: string[]
  rate_limit: { rpm: number | null; rpd: number | null }
  default_routing_policy: {
    prefer: 'latency' | 'capability'
    min_context: number | null
  }
}

export interface ApiKeyCreateInput {
  name: string
  provider_whitelist?: string[]
  provider_blacklist?: string[]
  rate_limit?: { rpm?: number; rpd?: number }
  default_routing_policy?: {
    prefer: 'latency' | 'capability'
    min_context?: number
  }
}

export interface ApiKeyCreated extends ApiKeyRow {
  id: string
  name: string
  key: string
  key_prefix: string
  created_at: string
}

export interface AcRouteStatus {
  available: boolean
  candidate_count: number
  recommended: boolean
  selected_model: string | null
}

export interface AcStatus {
  object: string
  available_model_count: number
  free_model_count: number
  distribution: Record<string, number>
  routes: Record<string, AcRouteStatus>
}

export interface AcModelInfo {
  id: string
  model_id: string
  provider_type: string | null
  provider_name: string | null
  category: string | null
  health_status: string
  route_eligible: boolean
  rate_limited_until: string | null
  last_success_at: string | null
  last_response_ms: number | null
  free_type: string | null
}

export interface AcModelsResponse {
  object: string
  data: AcModelInfo[]
}

export interface AcSelfTest {
  ok: boolean
  route: string
  code?: string
  message?: string
  selected_model: string | null
  candidate_count: number
  checked?: Array<{ model: string; ok: boolean; reason: string | null; retry_after?: number }>
}
