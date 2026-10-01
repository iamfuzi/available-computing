import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { poolApi, modelsApi, channelsApi, notificationsApi } from '../api/client'
import type { ModelRow, NotificationRow, PoolSummary } from '../api/client'
import { useWebSocket } from '../hooks/useWebSocket'
import StatCard from '../components/StatCard'
import HealthBadge from '../components/HealthBadge'
import FreshnessBadge from '../components/FreshnessBadge'
import FreeTypeBadge from '../components/FreeTypeBadge'
import AddChannelModal from '../components/AddChannelModal'

const CATEGORIES = ['全部', '文本', '多模态', '代码', '嵌入', '重排', '图像', '视频']
const CAT_MAP: Record<string, string> = { 文本: 'text', 多模态: 'vision', 代码: 'code', 嵌入: 'embedding', 重排: 'rerank', 图像: 'image', 视频: 'video' }

// 状态筛选三档：默认"可参与路由"与实际路由口径一致（healthy+slow）。
// "仅亚秒"是旧"仅健康"的准确名称——健康只是亚秒分档线，不是可用性线。
type StatusFilter = 'routable' | 'fast' | 'all'
const STATUS_FILTERS: [StatusFilter, string, string][] = [
  ['routable', '可参与路由', '健康 + 降权模型：实际参与 auto 路由调用的全部候选'],
  ['fast', '仅亚秒响应', '仅首响应 <1s 的健康模型（旧"仅健康"，只是快慢分档，不代表其他模型不可用）'],
  ['all', '全部状态', '含不可用、冷却中、待探测的模型（仅排查用）'],
]

function routingInfo(status: string | null): { label: string; dot: string; title: string } {
  switch (status) {
    case 'healthy':
      return { label: '优先', dot: 'bg-green-500', title: '参与 auto 路由，优先选择' }
    case 'slow':
      return { label: '降权可用', dot: 'bg-amber-400', title: '参与 auto 路由，排序靠后（响应 >1s）' }
    case 'rate_limited':
      return { label: '冷却中', dot: 'bg-purple-400', title: '上游 429 冷却中，暂不路由，到期自动恢复' }
    case 'down':
      return { label: '不路由', dot: 'bg-red-400', title: '探测失败，恢复后自动回池' }
    default:
      return { label: '待探测', dot: 'bg-gray-300', title: '尚未真实验证，不参与路由' }
  }
}

export default function Pool() {
  const [summary, setSummary] = useState<PoolSummary | null>(null)
  const [models, setModels] = useState<ModelRow[]>([])
  const [q, setQ] = useState('')
  const [category, setCategory] = useState('全部')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('routable')
  const [sortBy, setSortBy] = useState<'fast' | 'smart'>('fast')
  const [showAddModal, setShowAddModal] = useState(false)
  const [menuOpen, setMenuOpen] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [notifications, setNotifications] = useState<NotificationRow[]>([])
  const [channels, setChannels] = useState<{id: string, name: string, provider_type: string}[]>([])
  const [provider, setProvider] = useState('')
  const menuRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined)
  const navigate = useNavigate()

  const loadData = useCallback(async (signal?: AbortSignal) => {
    setLoading(true)
    try {
      const [s, m, n] = await Promise.all([
        poolApi.summary(),
        modelsApi.list({
          free_only: true,
          q: q || undefined,
          category: category !== '全部' ? CAT_MAP[category] : undefined,
          ...(statusFilter === 'routable'
            ? { routable_only: true }
            : statusFilter === 'fast'
              ? { healthy_only: true }
              : { healthy_only: false, hide_down: false, include_rate_limited: true }),
          provider: provider || undefined,
          sort_by: sortBy,
        }, signal),
        notificationsApi.list(),
      ])
      setSummary(s)
      setModels(m)
      setNotifications(n)
    } catch (e) {
      if (!(e instanceof Error && e.name === 'AbortError')) throw e
    } finally {
      setLoading(false)
    }
  }, [q, category, statusFilter, provider, sortBy])

  useEffect(() => {
    channelsApi.list().then(setChannels).catch(() => {})
  }, [])

  useEffect(() => {
    clearTimeout(debounceRef.current)
    abortRef.current?.abort()
    const ac = new AbortController()
    abortRef.current = ac
    debounceRef.current = setTimeout(() => loadData(ac.signal), 200)
    return () => { ac.abort(); clearTimeout(debounceRef.current) }
  }, [loadData])

  // Close menu on outside click
  useEffect(() => {
    if (!menuOpen) return
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(null)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [menuOpen])

  useWebSocket(() => { loadData() })

  function copyText(text: string) {
    navigator.clipboard.writeText(text)
  }

  function buildExample(m: ModelRow): string {
    const base = m.base_url || ''
    return `curl ${base}/chat/completions \\\n  -H "Authorization: Bearer <your-key>" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model":"${m.model_id}","messages":[{"role":"user","content":"Hello"}]}'`
  }

  const isEmpty = summary && summary.total_channels === 0

  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-gray-900">算力池总览</h1>
          <p className="text-xs text-gray-400 mt-0.5">
            {summary ? `${summary.enabled_channels} 个厂商 · ${summary.available_model_count} 个当前可调用` : '加载中...'}
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => loadData()}
            disabled={loading}
            className="text-sm border border-gray-200 rounded-lg px-3 py-1.5 hover:bg-white transition-colors disabled:opacity-50"
          >
            {loading ? '⏳' : '🔄'}
          </button>
        </div>
      </div>

      {/* Stats */}
      {summary && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <StatCard
            label="当前可用 / 计费待裁定"
            value={`${summary.available_model_count} / ${summary.pending_policy_change_count}`}
            onClick={() => summary.pending_policy_change_count > 0 ? navigate('/notifications') : undefined}
          />
          <StatCard
            label="失效或受限 Key"
            value={summary.invalid_key_count}
            onClick={() => navigate('/channels')}
          />
          <StatCard
            label="候选厂商待审核"
            value={summary.pending_candidate_count}
            onClick={() => navigate('/candidates')}
          />
          <StatCard label="24h 事件复检" value={summary.recheck_count_24h} />
        </div>
      )}

      {/* Active alerts stay separate from the model table. */}
      {notifications.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-2xl p-4 shadow-sm space-y-3">
          <div className="flex items-center justify-between">
            <div className="font-medium text-sm text-gray-900">需要处理</div>
            <button onClick={() => navigate('/notifications')} className="text-xs text-blue-600">查看全部 {notifications.length}</button>
          </div>
          <div className="grid md:grid-cols-2 gap-2">
            {notifications.slice(0, 4).map((item) => (
              <button
                key={item.id}
                onClick={async () => {
                  if (item.status === 'unread') await notificationsApi.update(item.id, 'read')
                  navigate(item.action_path || '/notifications')
                }}
                className={`text-left rounded-xl border px-3 py-2.5 ${
                  item.severity === 'critical' ? 'border-red-200 bg-red-50' :
                  item.severity === 'warning' ? 'border-amber-200 bg-amber-50' :
                  'border-blue-200 bg-blue-50'
                }`}
              >
                <div className="flex items-center gap-2 text-sm font-medium text-gray-900">
                  {item.status === 'unread' && <span className="w-2 h-2 rounded-full bg-red-500" />}
                  <span>{item.title}</span>
                </div>
                <p className="text-xs text-gray-500 mt-1 line-clamp-2">{item.message}</p>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Empty state */}
      {isEmpty && (
        <div className="bg-white border border-dashed border-gray-300 rounded-2xl p-12 text-center space-y-4">
          <div className="text-4xl">🔌</div>
          <p className="text-gray-600 font-medium">还没有接入任何厂商</p>
          <p className="text-sm text-gray-400">添加后系统自动探测免费模型，通常 1 分钟内完成</p>
          <div className="flex justify-center gap-3 pt-2">
            {([
              { id: 'groq', name: 'Groq', desc: '全免费' },
              { id: 'siliconflow', name: 'SiliconFlow', desc: '部分免费' },
              { id: 'agnes', name: 'Agnes AI', desc: 'flash 免费' },
            ] as const).map((p) => (
              <button
                key={p.id}
                onClick={() => setShowAddModal(true)}
                className="border border-blue-200 bg-blue-50 text-blue-700 rounded-xl px-4 py-3 text-sm hover:bg-blue-100 transition-colors"
              >
                <div className="font-medium">＋ {p.name}</div>
                <div className="text-xs text-blue-500 mt-0.5">{p.desc}</div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Filters + Table */}
      {!isEmpty && (
        <div className="bg-white border border-gray-200 rounded-2xl overflow-hidden shadow-sm">
          {/* Filter bar */}
          <div className="px-4 py-3 border-b border-gray-100 flex flex-wrap gap-2 items-center">
            <div className="flex gap-1 overflow-x-auto">
              {CATEGORIES.map((c) => (
                <button
                  key={c}
                  onClick={() => setCategory(c)}
                  className={`text-xs px-3 py-1.5 rounded-full whitespace-nowrap transition-colors ${
                    category === c
                      ? 'bg-gray-900 text-white'
                      : 'text-gray-500 hover:bg-gray-100'
                  }`}
                >
                  {c}
                </button>
              ))}
            </div>
            {channels.length > 1 && (() => {
              const unique = [...new Map(channels.map(c => [c.provider_type, c])).values()]
              return (
              <select
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
                className="text-xs border border-gray-200 rounded-lg px-2 py-1.5 text-gray-600 focus:outline-none focus:border-blue-400"
              >
                <option value="">全部厂商</option>
                {unique.map((ch) => (
                  <option key={ch.provider_type} value={ch.provider_type}>{ch.name}</option>
                ))}
              </select>
              )
            })()}
            <div className="flex items-center gap-1 bg-gray-100 rounded-lg p-0.5">
              {STATUS_FILTERS.map(([val, label, tip]) => (
                <button
                  key={val}
                  onClick={() => setStatusFilter(val)}
                  className={`text-xs px-2.5 py-1 rounded-md transition-colors whitespace-nowrap ${
                    statusFilter === val ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500'
                  }`}
                  title={tip}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-1 bg-gray-100 rounded-lg p-0.5">
              <button
                onClick={() => setSortBy('fast')}
                className={`text-xs px-2.5 py-1 rounded-md transition-colors ${
                  sortBy === 'fast' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500'
                }`}
                title="按响应速度排序（对应 auto:fast）"
              >
                最快
              </button>
              <button
                onClick={() => setSortBy('smart')}
                className={`text-xs px-2.5 py-1 rounded-md transition-colors ${
                  sortBy === 'smart' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500'
                }`}
                title="按参数量排序（对应 auto:smart）"
              >
                最聪明
              </button>
            </div>
            <div className="ml-auto relative">
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="搜索模型..."
                className="border border-gray-200 rounded-lg pl-8 pr-3 py-1.5 text-sm w-44 focus:outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-100 transition-shadow"
              />
              <span className="absolute left-2.5 top-2 text-gray-400 text-xs">🔍</span>
            </div>
          </div>

          {/* Loading */}
          {loading && models.length === 0 ? (
            <div className="flex items-center justify-center py-20 text-gray-400 text-sm">
              <span className="animate-pulse">加载中...</span>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-gray-50/80 text-gray-400 text-xs uppercase tracking-wider">
                  <tr>
                    <th className="px-4 py-3 text-left font-medium">厂商</th>
                    <th className="px-4 py-3 text-left font-medium">模型</th>
                    <th className="px-4 py-3 text-left font-medium hidden sm:table-cell">上下文</th>
                    <th className="px-4 py-3 text-left font-medium hidden md:table-cell">参数量</th>
                    <th className="px-4 py-3 text-left font-medium hidden md:table-cell">免费类型</th>
                    <th className="px-4 py-3 text-left font-medium">路由</th>
                    <th className="px-4 py-3 text-left font-medium">状态</th>
                    <th className="px-4 py-3 w-10"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {models.map((m) => (
                    <tr
                      key={m.id}
                      className="hover:bg-blue-50/40 cursor-pointer transition-colors"
                      onClick={() => navigate(`/models/${m.id}`)}
                    >
                      <td className="px-4 py-3">
                        <span className="inline-flex items-center gap-1.5 text-gray-600">
                          <span className="w-1.5 h-1.5 rounded-full bg-blue-400" />
                          {m.provider_name}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <div className="font-medium text-gray-900 font-mono text-xs">{m.model_id}</div>
                      </td>
                      <td className="px-4 py-3 text-gray-500 hidden sm:table-cell">
                        {m.context_length ? `${Math.round(m.context_length / 1000)}K` : '—'}
                      </td>
                      <td className="px-4 py-3 text-gray-500 hidden md:table-cell">
                        {m.param_size != null
                          ? (m.param_size >= 1
                              ? `${m.param_size % 1 === 0 ? m.param_size.toFixed(0) : m.param_size}B`
                              : `${m.param_size}B`)
                          : '—'}
                      </td>
                      <td className="px-4 py-3 hidden md:table-cell">
                        <FreeTypeBadge freeType={m.free_type} source={m.free_source} isFree={m.is_free} />
                      </td>
                      <td className="px-4 py-3">
                        {(() => {
                          const ri = routingInfo(m.health_status)
                          return (
                            <span className="inline-flex items-center gap-1.5 text-xs text-gray-600" title={ri.title}>
                              <span className={`w-1.5 h-1.5 rounded-full ${ri.dot}`} />
                              {ri.label}
                            </span>
                          )
                        })()}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex flex-col items-start gap-1">
                          <HealthBadge status={m.health_status} responseMs={m.last_response_ms} />
                          <FreshnessBadge
                            lastVerifiedAt={m.last_verified_at}
                            thresholdDays={m.staleness_threshold_days}
                            method={m.verification_method}
                          />
                        </div>
                      </td>
                      <td className="px-4 py-3 relative" onClick={(e) => e.stopPropagation()}>
                        <div ref={menuOpen === m.id ? menuRef : undefined}>
                          <button
                            onClick={() => setMenuOpen(menuOpen === m.id ? null : m.id)}
                            className="text-gray-300 hover:text-gray-600 px-1 transition-colors"
                          >
                            ⋯
                          </button>
                          {menuOpen === m.id && (
                            <div className="absolute right-2 top-9 bg-white border border-gray-200 rounded-xl shadow-lg z-10 w-44 py-1 text-sm">
                              <button
                                className="w-full text-left px-4 py-2.5 hover:bg-gray-50 text-gray-700 transition-colors"
                                onClick={() => { copyText(m.base_url || ''); setMenuOpen(null) }}
                              >
                                复制 Endpoint
                              </button>
                              <button
                                className="w-full text-left px-4 py-2.5 hover:bg-gray-50 text-gray-700 transition-colors"
                                onClick={() => { copyText(buildExample(m)); setMenuOpen(null) }}
                              >
                                复制调用示例
                              </button>
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {models.length === 0 && !loading && (
            <p className="text-center text-gray-400 text-sm py-12">没有符合条件的模型</p>
          )}

          <div className="px-4 py-2.5 text-xs text-gray-400 border-t border-gray-100">
            共 {models.length} 个模型 · {STATUS_FILTERS.find(([v]) => v === statusFilter)?.[1]} · {sortBy === 'smart' ? '按参数量排序' : '按响应速度排序'}
          </div>
        </div>
      )}

      <AddChannelModal
        open={showAddModal}
        onClose={() => setShowAddModal(false)}
        onCreated={loadData}
      />
    </div>
  )
}
