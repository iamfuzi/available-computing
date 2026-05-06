import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { poolApi, modelsApi } from '../api/client'
import type { ModelRow, PoolSummary } from '../api/client'
import { useWebSocket } from '../hooks/useWebSocket'
import StatCard from '../components/StatCard'
import HealthBadge from '../components/HealthBadge'
import FreeTypeBadge from '../components/FreeTypeBadge'
import AddChannelModal from '../components/AddChannelModal'

const CATEGORIES = ['全部', '文本', '多模态', '代码', '嵌入']
const CAT_MAP: Record<string, string> = { 文本: 'text', 多模态: 'vision', 代码: 'code', 嵌入: 'embedding' }

export default function Pool() {
  const [summary, setSummary] = useState<PoolSummary | null>(null)
  const [models, setModels] = useState<ModelRow[]>([])
  const [q, setQ] = useState('')
  const [category, setCategory] = useState('全部')
  const [healthyOnly, setHealthyOnly] = useState(false)
  const [showAddModal, setShowAddModal] = useState(false)
  const [menuOpen, setMenuOpen] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const menuRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined)
  const navigate = useNavigate()

  const loadData = useCallback(async (signal?: AbortSignal) => {
    setLoading(true)
    try {
      const [s, m] = await Promise.all([
        poolApi.summary(),
        modelsApi.list({
          free_only: true,
          q: q || undefined,
          category: category !== '全部' ? CAT_MAP[category] : undefined,
          healthy_only: healthyOnly || undefined,
        }, signal),
      ])
      setSummary(s)
      setModels(m)
    } catch (e) {
      if (!(e instanceof Error && e.name === 'AbortError')) throw e
    } finally {
      setLoading(false)
    }
  }, [q, category, healthyOnly])

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

  useWebSocket((_event) => { loadData() })

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
            {summary ? `${summary.enabled_channels} 个厂商 · ${summary.free_model_count} 个免费模型` : '加载中...'}
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
            label="接入厂商"
            value={`${summary.enabled_channels}/${summary.total_channels}`}
            onClick={() => navigate('/channels')}
          />
          <StatCard label="免费模型" value={summary.free_model_count} />
          <StatCard
            label="健康可用"
            value={summary.health_distribution.healthy ?? 0}
            sub={
              [summary.health_distribution.slow, summary.health_distribution.down]
                .filter(Boolean)
                .length
                ? `慢 ${summary.health_distribution.slow ?? 0} · 异常 ${summary.health_distribution.down ?? 0}`
                : undefined
            }
          />
          <StatCard
            label="平均延迟"
            value={
              models.length > 0 && models.some(m => m.last_response_ms)
                ? `${Math.round(models.filter(m => m.last_response_ms).reduce((s, m) => s + (m.last_response_ms || 0), 0) / models.filter(m => m.last_response_ms).length)}ms`
                : '—'
            }
          />
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
              { id: 'gemini', name: 'Gemini', desc: '免费额度' },
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
            <label className="flex items-center gap-1.5 text-xs text-gray-500 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={healthyOnly}
                onChange={(e) => setHealthyOnly(e.target.checked)}
                className="rounded border-gray-300"
              />
              仅健康
            </label>
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
                    <th className="px-4 py-3 text-left font-medium hidden md:table-cell">免费类型</th>
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
                      <td className="px-4 py-3 hidden md:table-cell">
                        <FreeTypeBadge freeType={m.free_type} source={m.free_source} />
                      </td>
                      <td className="px-4 py-3">
                        <HealthBadge status={m.health_status} responseMs={m.last_response_ms} />
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
            共 {models.length} 个模型 · 按响应时间排序
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
