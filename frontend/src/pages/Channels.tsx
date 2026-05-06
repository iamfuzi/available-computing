import { useState, useEffect } from 'react'
import { channelsApi } from '../api/client'
import type { Channel } from '../api/client'
import AddChannelModal from '../components/AddChannelModal'

export default function Channels() {
  const [channels, setChannels] = useState<Channel[]>([])
  const [showAddModal, setShowAddModal] = useState(false)
  const [probingId, setProbingId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  async function load() {
    setLoading(true)
    setError('')
    try {
      setChannels(await channelsApi.list())
    } catch {
      setError('加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  async function handleProbe(id: string) {
    setProbingId(id)
    setError('')
    try {
      await channelsApi.probe(id)
      setTimeout(() => { setProbingId(null); load() }, 2000)
    } catch {
      setProbingId(null)
      setError('探测失败')
    }
  }

  async function handleToggle(ch: Channel) {
    setError('')
    try {
      await channelsApi.update(ch.id, { enabled: !ch.enabled })
      load()
    } catch {
      setError('操作失败')
    }
  }

  async function handleDelete(id: string) {
    if (!confirm('确认删除该厂商及其所有模型数据？')) return
    setError('')
    try {
      await channelsApi.delete(id)
      load()
    } catch {
      setError('删除失败')
    }
  }

  return (
    <div className="max-w-3xl mx-auto px-4 py-6 space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-gray-900">厂商管理</h1>
          <p className="text-xs text-gray-400 mt-0.5">管理你的 API Key 和接入状态</p>
        </div>
        <button
          onClick={() => setShowAddModal(true)}
          className="bg-gray-900 text-white text-sm px-4 py-2 rounded-xl hover:bg-gray-800 transition-colors"
        >
          ＋ 添加厂商
        </button>
      </div>

      {error && (
        <p className="text-sm text-red-600 bg-red-50 border border-red-100 rounded-xl px-4 py-2">{error}</p>
      )}

      {loading && channels.length === 0 ? (
        <div className="text-center py-20 text-gray-400 text-sm animate-pulse">加载中...</div>
      ) : channels.length === 0 ? (
        <div className="bg-white border border-dashed border-gray-300 rounded-2xl p-12 text-center space-y-3">
          <div className="text-3xl">🔌</div>
          <p className="text-gray-500">还没有接入任何厂商</p>
          <button
            onClick={() => setShowAddModal(true)}
            className="text-sm text-blue-600 hover:text-blue-800"
          >
            添加第一个厂商 →
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          {channels.map((ch) => (
            <div
              key={ch.id}
              className={`bg-white border rounded-xl p-4 space-y-3 transition-opacity ${
                ch.enabled ? 'border-gray-200 shadow-sm' : 'border-gray-100 opacity-50'
              }`}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2.5">
                  <span className={`w-2 h-2 rounded-full ${ch.enabled ? 'bg-green-500' : 'bg-gray-300'}`} />
                  <span className="font-semibold text-gray-900">{ch.name}</span>
                  <span className="text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full">
                    {ch.provider_type}
                  </span>
                </div>
                {ch.free_model_count > 0 ? (
                  <span className="text-sm font-medium text-green-700 bg-green-50 px-2.5 py-0.5 rounded-full">
                    {ch.free_model_count} 个免费模型
                  </span>
                ) : (
                  <span className="text-xs text-gray-400 animate-pulse">探测中...</span>
                )}
              </div>

              <div className="flex items-center justify-between text-sm text-gray-500">
                <code className="bg-gray-50 text-gray-600 px-2 py-0.5 rounded text-xs font-mono">
                  {ch.api_key_hint}
                </code>
                <span className="text-xs">
                  {ch.last_probed_at
                    ? `探测于 ${new Date(ch.last_probed_at).toLocaleString()}`
                    : '尚未探测'}
                </span>
              </div>

              <div className="flex gap-2 pt-1 border-t border-gray-50">
                <button
                  onClick={() => handleProbe(ch.id)}
                  disabled={probingId === ch.id}
                  className="text-xs border border-gray-200 px-3 py-1.5 rounded-lg hover:bg-gray-50 disabled:opacity-50 transition-colors"
                >
                  {probingId === ch.id ? '探测中...' : '🔄 刷新'}
                </button>
                <button
                  onClick={() => handleToggle(ch)}
                  className={`text-xs border px-3 py-1.5 rounded-lg transition-colors ${
                    ch.enabled
                      ? 'border-gray-200 hover:bg-gray-50'
                      : 'border-green-200 text-green-700 hover:bg-green-50'
                  }`}
                >
                  {ch.enabled ? '禁用' : '启用'}
                </button>
                <button
                  onClick={() => handleDelete(ch.id)}
                  className="text-xs border border-transparent text-gray-400 hover:text-red-600 hover:border-red-200 px-3 py-1.5 rounded-lg transition-colors ml-auto"
                >
                  删除
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <AddChannelModal
        open={showAddModal}
        onClose={() => setShowAddModal(false)}
        onCreated={load}
      />
    </div>
  )
}
