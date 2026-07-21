import { useState, useEffect } from 'react'
import { settingsApi, apiKeysApi } from '../api/client'
import type { Settings as SettingsType, ApiKeyRow, ApiKeyCreated } from '../api/client'

export default function SettingsPage() {
  const [settings, setSettings] = useState<SettingsType | null>(null)
  const [saved, setSaved] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // API Keys state
  const [apiKeys, setApiKeys] = useState<ApiKeyRow[]>([])
  const [newKeyName, setNewKeyName] = useState('')
  const [createdKey, setCreatedKey] = useState<ApiKeyCreated | null>(null)
  const [copiedNew, setCopiedNew] = useState(false)
  const [copiedKey, setCopiedKey] = useState<string | null>(null)
  const [keyLoading, setKeyLoading] = useState(false)
  const [showKeyPolicy, setShowKeyPolicy] = useState(false)
  const [newWhitelist, setNewWhitelist] = useState('')
  const [newBlacklist, setNewBlacklist] = useState('')
  const [newRpm, setNewRpm] = useState('')
  const [newRpd, setNewRpd] = useState('')
  const [newMinContext, setNewMinContext] = useState('')
  const [newPrefer, setNewPrefer] = useState<'latency' | 'capability'>('latency')

  useEffect(() => {
    Promise.all([settingsApi.get(), apiKeysApi.list()])
      .then(([s, keys]) => { setSettings(s); setApiKeys(keys); setLoading(false) })
      .catch(() => { setError('加载失败'); setLoading(false) })
  }, [])

  function loadKeys() {
    apiKeysApi.list().then(setApiKeys).catch(() => {})
  }

  if (loading) return <div className="p-8 text-gray-400 text-sm animate-pulse">加载中...</div>
  if (!settings) return null

  async function handleSave() {
    setError('')
    try {
      await settingsApi.update({
        discovery_interval_hours: settings!.discovery_interval_hours,
        probe_interval_hours: settings!.probe_interval_hours,
        slow_threshold_ms: settings!.slow_threshold_ms,
      })
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch {
      setError('保存失败，请重试')
    }
  }

  async function handleCreateKey() {
    if (!newKeyName.trim()) return
    setKeyLoading(true)
    try {
      const splitProviders = (value: string) => value
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean)
      const created = await apiKeysApi.create({
        name: newKeyName.trim(),
        provider_whitelist: splitProviders(newWhitelist),
        provider_blacklist: splitProviders(newBlacklist),
        rate_limit: {
          ...(newRpm ? { rpm: Number(newRpm) } : {}),
          ...(newRpd ? { rpd: Number(newRpd) } : {}),
        },
        default_routing_policy: {
          prefer: newPrefer,
          ...(newMinContext ? { min_context: Number(newMinContext) } : {}),
        },
      })
      setCreatedKey(created)
      setNewKeyName('')
      setNewWhitelist('')
      setNewBlacklist('')
      setNewRpm('')
      setNewRpd('')
      setNewMinContext('')
      setNewPrefer('latency')
      loadKeys()
    } catch {
      setError('创建密钥失败')
    } finally {
      setKeyLoading(false)
    }
  }

  async function handleDeleteKey(id: string) {
    try {
      await apiKeysApi.delete(id)
      loadKeys()
    } catch {
      setError('删除密钥失败')
    }
  }

  async function handleToggleKey(key: ApiKeyRow) {
    try {
      await apiKeysApi.update(key.id, { is_active: !key.is_active })
      loadKeys()
    } catch {
      setError('更新密钥状态失败')
    }
  }

  function copyText(text: string, setter?: (v: boolean) => void) {
    navigator.clipboard.writeText(text)
    if (setter) {
      setter(true)
      setTimeout(() => setter(false), 2000)
    }
  }

  function fmtDate(iso: string | null) {
    if (!iso) return '—'
    const d = new Date(iso)
    const now = new Date()
    const diffMs = now.getTime() - d.getTime()
    const diffMin = Math.floor(diffMs / 60000)
    if (diffMin < 1) return '刚刚'
    if (diffMin < 60) return `${diffMin} 分钟前`
    const diffH = Math.floor(diffMin / 60)
    if (diffH < 24) return `${diffH} 小时前`
    return d.toLocaleDateString('zh-CN')
  }

  return (
    <div className="max-w-2xl mx-auto px-4 py-6 space-y-5">
      <div>
        <h1 className="text-lg font-bold text-gray-900">设置</h1>
        <p className="text-xs text-gray-400 mt-0.5">系统参数和偏好</p>
      </div>

      {/* API Keys */}
      <div className="bg-white border border-gray-200 rounded-2xl p-5 space-y-4 shadow-sm">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-gray-900">API 密钥</h2>
          <span className="text-xs text-gray-400">外部服务通过密钥调用</span>
        </div>

        {/* Created key banner */}
        {createdKey && (
          <div className="bg-green-50 border border-green-200 rounded-xl p-4 space-y-2">
            <p className="text-xs text-green-700 font-medium">密钥创建成功，请尽快复制保存（离开页面后将无法再次查看）</p>
            <div className="flex items-center gap-2">
              <code className="flex-1 bg-white border border-green-200 rounded-lg px-3 py-2 text-sm font-mono text-green-900 break-all select-all">
                {createdKey.key}
              </code>
              <button
                onClick={() => copyText(createdKey.key, setCopiedNew)}
                className="shrink-0 text-xs border border-green-300 text-green-700 rounded-lg px-3 py-2 hover:bg-green-100 transition-colors"
              >
                {copiedNew ? '✓ 已复制' : '复制'}
              </button>
            </div>
          </div>
        )}

        {/* Key list */}
        {apiKeys.length > 0 ? (
          <div className="divide-y divide-gray-50">
            {apiKeys.map((k) => (
              <div key={k.id} className="flex items-center gap-3 py-3 first:pt-0 last:pb-0">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-gray-900 truncate">{k.name}</span>
                    <button
                      onClick={() => handleToggleKey(k)}
                      className={`text-[10px] px-1.5 py-0.5 rounded-full border ${
                        k.is_active
                          ? 'bg-green-50 text-green-700 border-green-200'
                          : 'bg-gray-50 text-gray-400 border-gray-200'
                      }`}
                    >
                      {k.is_active ? '启用' : '停用'}
                    </button>
                  </div>
                  <div className="flex items-center gap-2 mt-1">
                    <code className="text-xs font-mono text-gray-400 select-all">{k.key || k.key_prefix}</code>
                    {k.key && (
                      <button
                        onClick={() => { navigator.clipboard.writeText(k.key); setCopiedKey(k.id); setTimeout(() => setCopiedKey(null), 2000) }}
                        className="text-gray-300 hover:text-gray-600 transition-colors"
                        title="复制密钥"
                      >
                        {copiedKey === k.id ? '✓' : '📋'}
                      </button>
                    )}
                    <span className="text-xs text-gray-300">创建于 {fmtDate(k.created_at)}</span>
                  </div>
                  {(k.provider_whitelist.length > 0 || k.provider_blacklist.length > 0 || k.rate_limit.rpm || k.rate_limit.rpd || k.default_routing_policy.min_context) && (
                    <div className="flex flex-wrap gap-1 mt-1.5 text-[10px] text-gray-500">
                      {k.provider_whitelist.length > 0 && <span className="bg-blue-50 px-1.5 py-0.5 rounded">仅 {k.provider_whitelist.join(', ')}</span>}
                      {k.provider_blacklist.length > 0 && <span className="bg-red-50 px-1.5 py-0.5 rounded">排除 {k.provider_blacklist.join(', ')}</span>}
                      {k.rate_limit.rpm && <span className="bg-gray-50 px-1.5 py-0.5 rounded">{k.rate_limit.rpm} RPM</span>}
                      {k.rate_limit.rpd && <span className="bg-gray-50 px-1.5 py-0.5 rounded">{k.rate_limit.rpd} RPD</span>}
                      {k.default_routing_policy.min_context && <span className="bg-gray-50 px-1.5 py-0.5 rounded">≥ {Math.round(k.default_routing_policy.min_context / 1000)}K 上下文</span>}
                    </div>
                  )}
                </div>
                <button
                  onClick={() => handleDeleteKey(k.id)}
                  className="text-xs text-gray-300 hover:text-red-500 transition-colors shrink-0"
                >
                  删除
                </button>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-gray-400 text-center py-4">还没有创建 API 密钥</p>
        )}

        {/* Create form */}
        <div className="space-y-3 pt-2 border-t border-gray-50">
          <div className="flex gap-2">
            <input
              value={newKeyName}
              onChange={(e) => setNewKeyName(e.target.value)}
              placeholder="输入名称，如「我的应用」"
              className="flex-1 border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-50 transition-shadow"
              onKeyDown={(e) => e.key === 'Enter' && !showKeyPolicy && handleCreateKey()}
            />
            <button
              type="button"
              onClick={() => setShowKeyPolicy(!showKeyPolicy)}
              className="border border-gray-200 text-gray-500 text-xs rounded-lg px-3 py-2 hover:bg-gray-50"
            >
              {showKeyPolicy ? '收起策略' : '路由策略'}
            </button>
            <button
              onClick={handleCreateKey}
              disabled={!newKeyName.trim() || keyLoading}
              className="bg-gray-900 text-white text-sm rounded-lg px-4 py-2 hover:bg-gray-800 transition-colors disabled:opacity-50 shrink-0"
            >
              创建密钥
            </button>
          </div>
          {showKeyPolicy && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 bg-gray-50 rounded-xl p-3">
              <input value={newWhitelist} onChange={(e) => setNewWhitelist(e.target.value)} placeholder="允许厂商，逗号分隔（留空=全部）" className="border border-gray-200 rounded-lg px-3 py-2 text-xs" />
              <input value={newBlacklist} onChange={(e) => setNewBlacklist(e.target.value)} placeholder="排除厂商，逗号分隔" className="border border-gray-200 rounded-lg px-3 py-2 text-xs" />
              <input type="number" min="1" value={newRpm} onChange={(e) => setNewRpm(e.target.value)} placeholder="每分钟上限 RPM" className="border border-gray-200 rounded-lg px-3 py-2 text-xs" />
              <input type="number" min="1" value={newRpd} onChange={(e) => setNewRpd(e.target.value)} placeholder="每日上限 RPD" className="border border-gray-200 rounded-lg px-3 py-2 text-xs" />
              <input type="number" min="1" value={newMinContext} onChange={(e) => setNewMinContext(e.target.value)} placeholder="最小上下文，如 32000" className="border border-gray-200 rounded-lg px-3 py-2 text-xs" />
              <select value={newPrefer} onChange={(e) => setNewPrefer(e.target.value as 'latency' | 'capability')} className="border border-gray-200 rounded-lg px-3 py-2 text-xs bg-white">
                <option value="latency">优先低延迟</option>
                <option value="capability">优先大模型</option>
              </select>
              <p className="sm:col-span-2 text-[11px] text-gray-400">请求级策略可以继续排除厂商或提高上下文下限，但不能放宽这里的权限。</p>
            </div>
          )}
        </div>
      </div>

      <div className="bg-white border border-gray-200 rounded-2xl p-5 space-y-4 shadow-sm">
        <h2 className="text-sm font-semibold text-gray-900">探测设置</h2>

        <div className="space-y-3">
          {([
            { key: 'discovery_interval_hours', label: '自动刷新频率', unit: '小时', min: 1, max: 48 },
            { key: 'probe_interval_hours', label: '主动探测频率', unit: '小时', min: 1, max: 24 },
            { key: 'slow_threshold_ms', label: '慢速阈值', unit: 'ms', min: 100, max: 10000 },
          ] as const).map((item) => (
            <label key={item.key} className="flex items-center justify-between gap-4">
              <span className="text-sm text-gray-600">{item.label}</span>
              <div className="flex items-center gap-2">
                <input
                  type="number"
                  min={item.min}
                  max={item.max}
                  value={settings[item.key]}
                  onChange={(e) => setSettings({ ...settings, [item.key]: e.target.value })}
                  className="border border-gray-200 rounded-lg px-3 py-1.5 text-sm w-20 text-right focus:outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-50 transition-shadow"
                />
                <span className="text-xs text-gray-400 w-8">{item.unit}</span>
              </div>
            </label>
          ))}
        </div>
      </div>

      <div className="bg-white border border-gray-200 rounded-2xl p-5 space-y-3 shadow-sm">
        <h2 className="text-sm font-semibold text-gray-900">白名单</h2>
        <div className="flex items-center justify-between">
          <span className="text-sm text-gray-500">当前版本</span>
          <code className="bg-gray-50 px-2 py-0.5 rounded text-xs font-mono">{settings.whitelist_version}</code>
        </div>
      </div>

      <div className="bg-white border border-gray-200 rounded-2xl p-5 space-y-3 shadow-sm">
        <h2 className="text-sm font-semibold text-gray-900">安全</h2>
        <div className="flex items-center justify-between">
          <span className="text-sm text-gray-500">Key 加密</span>
          <span className="text-xs bg-green-50 text-green-700 border border-green-200 px-2.5 py-0.5 rounded-full">
            始终开启
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-sm text-gray-500">数据存储</span>
          <span className="text-xs text-gray-500">本地 SQLite，不上云</span>
        </div>
      </div>

      {error && (
        <p className="text-sm text-red-600 bg-red-50 border border-red-100 rounded-xl px-4 py-2">{error}</p>
      )}

      <button
        onClick={handleSave}
        disabled={saved}
        className={`w-full rounded-xl py-2.5 text-sm font-medium transition-colors ${
          saved
            ? 'bg-green-50 text-green-700 border border-green-200'
            : 'bg-gray-900 text-white hover:bg-gray-800'
        }`}
      >
        {saved ? '✓ 已保存' : '保存设置'}
      </button>

      <div className="text-center text-xs text-gray-300 pt-4">
        算力池 v0.1.0 · <a href="https://github.com/iamfuzi/available-computing" target="_blank" rel="noreferrer" className="hover:text-gray-500">GitHub</a>
      </div>
    </div>
  )
}
