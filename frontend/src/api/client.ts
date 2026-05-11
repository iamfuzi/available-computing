import axios from 'axios'

const api = axios.create({ baseURL: '/api/v1' })

api.interceptors.request.use((config) => {
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
  create: (name: string) => api.post<ApiKeyCreated>('/apikeys', { name }).then((r) => r.data),
  update: (id: string, data: { is_active?: boolean; name?: string }) =>
    api.patch(`/apikeys/${id}`, data),
  delete: (id: string) => api.delete(`/apikeys/${id}`),
}

// Types
export interface PoolSummary {
  total_channels: number
  enabled_channels: number
  free_model_count: number
  health_distribution: Record<string, number>
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
  free_model_count: number
}

export interface Provider {
  id: string
  name: string
  base_url: string
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
  provider_type: string | null
  provider_name: string | null
  base_url: string | null
}

export interface ModelListParams {
  provider?: string
  category?: string
  free_only?: boolean
  healthy_only?: boolean
  q?: string
}

export interface HealthRecord {
  id: number
  model_id: string
  checked_at: string
  status: string
  response_ms: number | null
  error_code: string | null
  is_passive: boolean
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
}

export interface ApiKeyCreated {
  id: string
  name: string
  key: string
  key_prefix: string
  created_at: string
}
