import { useState, useEffect, useCallback } from 'react'
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
  const navigate = useNavigate()

  const loadData = useCallback(async () => {
    const [s, m] = await Promise.all([
      poolApi.summary(),
      modelsApi.list({ free_only: true, q: q || undefined, category: category !== '全部' ? CAT_MAP[category] : undefined, healthy_only: healthyOnly || undefined }),
    ])
    setSummary(s)
    setModels(m)
  }, [q, category, healthyOnly])

  useEffect(() => { loadData() }, [loadData])

  useWebSocket((event) => {
    if (event === 'pool_updated') loadData()
  })

  function copyText(text: string) {
    navigator.clipboard.writeText(text)
  }

  function buildExample(m: ModelRow): string {
    const base = m.base_url || ''
    return `curl ${base}/chat/completions \\\n  -H "Authorization: Bearer <your-key>" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model":"${m.model_id}","messages":[{"role":"user","content":"Hello"}]}'`
  }

  const isEmpty = summary && summary.total_channels === 0

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-7xl mx-auto px-4 py-6 space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-semibold text-gray-900">📊 算力池总览</h1>
          <div className="flex gap-2">
            <button onClick={loadData} className="text-sm border border-gray-300 rounded-lg px-3 py-1.5 hover:bg-white">🔄 刷新</button>
            <button onClick={() => navigate('/settings')} className="text-sm border border-gray-300 rounded-lg px-3 py-1.5 hover:bg-white">⚙️</button>
          </div>
        </div>

        {/* Stats */}
        {summary && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard
              label="接入厂商"
              value={`${summary.enabled_channels} / ${summary.total_channels}`}
              onClick={() => navigate('/channels')}
            />
            <StatCard label="免费模型" value={summary.free_model_count} />
            <StatCard
              label="健康可用"
              value={summary.health_distribution.healthy ?? 0}
              sub={`慢 ${summary.health_distribution.slow ?? 0}  异常 ${summary.health_distribution.down ?? 0}`}
            />
            <StatCard label="今日调用" value="—" sub="V1.0+" />
          </div>
        )}

        {/* Empty state */}
        {isEmpty && (
          <div className="bg-white border border-gray-200 rounded-2xl p-10 text-center space-y-4">
            <p className="text-gray-600 font-medium">还没有接入任何厂商</p>
            <p className="text-sm text-gray-400">添加后系统自动探测免费模型，通常 1 分钟内完成</p>
            <div className="flex justify-center gap-3 mt-4">
              {['groq', 'siliconflow', 'gemini'].map((id) => (
                <button
                  key={id}
                  onClick={() => setShowAddModal(true)}
                  className="border border-blue-300 text-blue-700 rounded-lg px-4 py-2 text-sm hover:bg-blue-50"
                >
                  ＋ {id === 'groq' ? 'Groq' : id === 'siliconflow' ? 'SiliconFlow' : 'Gemini'}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Filters + Table */}
        {!isEmpty && (
          <div className="bg-white border border-gray-200 rounded-2xl overflow-hidden">
            {/* Filter bar */}
            <div className="px-4 py-3 border-b border-gray-100 flex flex-wrap gap-3 items-center">
              <div className="flex gap-1">
                {CATEGORIES.map((c) => (
                  <button
                    key={c}
                    onClick={() => setCategory(c)}
                    className={`text-sm px-3 py-1 rounded-lg ${category === c ? 'bg-blue-600 text-white' : 'text-gray-600 hover:bg-gray-100'}`}
                  >
                    {c}
                  </button>
                ))}
              </div>
              <label className="flex items-center gap-1.5 text-sm text-gray-600 cursor-pointer">
                <input type="checkbox" checked={healthyOnly} onChange={(e) => setHealthyOnly(e.target.checked)} />
                仅显示健康
              </label>
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="🔍 搜索模型..."
                className="ml-auto border border-gray-200 rounded-lg px-3 py-1.5 text-sm w-48 focus:outline-none focus:border-blue-400"
              />
            </div>

            {/* Table */}
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-gray-500 text-xs uppercase">
                <tr>
                  <th className="px-4 py-3 text-left">厂商</th>
                  <th className="px-4 py-3 text-left">模型名</th>
                  <th className="px-4 py-3 text-left">类型</th>
                  <th className="px-4 py-3 text-left">上下文</th>
                  <th className="px-4 py-3 text-left">免费类型</th>
                  <th className="px-4 py-3 text-left">状态</th>
                  <th className="px-4 py-3 text-left w-8"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {models.map((m) => (
                  <tr
                    key={m.id}
                    className="hover:bg-gray-50 cursor-pointer"
                    onClick={() => navigate(`/models/${m.id}`)}
                  >
                    <td className="px-4 py-3 text-gray-700">{m.provider_name}</td>
                    <td className="px-4 py-3 font-medium text-gray-900">{m.model_id}</td>
                    <td className="px-4 py-3 text-gray-500 capitalize">{m.category || '—'}</td>
                    <td className="px-4 py-3 text-gray-500">
                      {m.context_length ? `${Math.round(m.context_length / 1000)}K` : '—'}
                    </td>
                    <td className="px-4 py-3">
                      <FreeTypeBadge freeType={m.free_type} source={m.free_source} />
                    </td>
                    <td className="px-4 py-3">
                      <HealthBadge status={m.health_status} responseMs={m.last_response_ms} />
                    </td>
                    <td className="px-4 py-3 relative" onClick={(e) => e.stopPropagation()}>
                      <button
                        onClick={() => setMenuOpen(menuOpen === m.id ? null : m.id)}
                        className="text-gray-400 hover:text-gray-700 px-1"
                      >
                        ⋯
                      </button>
                      {menuOpen === m.id && (
                        <div className="absolute right-4 top-8 bg-white border border-gray-200 rounded-lg shadow-lg z-10 w-44 py-1 text-sm">
                          <button
                            className="w-full text-left px-4 py-2 hover:bg-gray-50"
                            onClick={() => { copyText(m.base_url || ''); setMenuOpen(null) }}
                          >
                            复制 Endpoint
                          </button>
                          <button
                            className="w-full text-left px-4 py-2 hover:bg-gray-50"
                            onClick={() => { copyText(buildExample(m)); setMenuOpen(null) }}
                          >
                            复制调用示例
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {models.length === 0 && (
              <p className="text-center text-gray-400 text-sm py-10">没有符合条件的模型</p>
            )}

            <div className="px-4 py-3 text-xs text-gray-400 border-t border-gray-100">
              共 {models.length} 个模型
            </div>
          </div>
        )}
      </div>

      <AddChannelModal
        open={showAddModal}
        onClose={() => setShowAddModal(false)}
        onCreated={loadData}
      />
    </div>
  )
}
