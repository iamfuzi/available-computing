import { useState, useEffect } from 'react'
import { channelsApi } from '../api/client'
import type { Channel } from '../api/client'
import AddChannelModal from '../components/AddChannelModal'

export default function Channels() {
  const [channels, setChannels] = useState<Channel[]>([])
  const [showAddModal, setShowAddModal] = useState(false)
  const [editChannel, setEditChannel] = useState<Channel | null>(null)
  const [editKey, setEditKey] = useState('')
  const [editName, setEditName] = useState('')
  const [editBaseUrl, setEditBaseUrl] = useState('')
  const [editLoading, setEditLoading] = useState(false)
  const [editError, setEditError] = useState('')
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

  function openEdit(ch: Channel) {
    setEditChannel(ch)
    setEditName(ch.name)
    setEditKey('')
    setEditBaseUrl(ch.base_url || '')
    setEditError('')
  }

  async function handleEditSave() {
    if (!editChannel) return
    setEditLoading(true)
    setEditError('')
    try {
      const data: Record<string, string> = {}
      if (editName.trim() && editName !== editChannel.name) data.name = editName.trim()
      if (editBaseUrl.trim() !== (editChannel.base_url || '')) data.base_url = editBaseUrl.trim() || ''
      if (editKey.trim()) data.api_key = editKey.trim()
      if (Object.keys(data).length > 0) {
        await channelsApi.update(editChannel.id, data)
      }
      setEditChannel(null)
      load()
    } catch {
      setEditError('保存失败')
    } finally {
      setEditLoading(false)
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
                  onClick={() => openEdit(ch)}
                  className="text-xs border border-gray-200 px-3 py-1.5 rounded-lg hover:bg-gray-50 transition-colors"
                >
                  ✏️ 编辑
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

      {/* Edit modal */}
      {editChannel && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md">
            <div className="flex justify-between items-center px-6 py-4 border-b border-gray-100">
              <h2 className="text-base font-semibold text-gray-900">编辑 {editChannel.name}</h2>
              <button onClick={() => setEditChannel(null)} className="text-gray-300 hover:text-gray-600 text-lg leading-none transition-colors">✕</button>
            </div>
            <div className="p-6 space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1.5">名称</label>
                <input
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  className="w-full border border-gray-200 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-50 transition-shadow"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1.5">
                  API Key <span className="text-gray-400 font-normal text-xs">留空则不修改</span>
                </label>
                <input
                  type="password"
                  value={editKey}
                  onChange={(e) => setEditKey(e.target.value)}
                  placeholder={`当前: ${editChannel.api_key_hint}`}
                  className="w-full border border-gray-200 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-50 transition-shadow font-mono"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1.5">Base URL</label>
                <input
                  value={editBaseUrl}
                  onChange={(e) => setEditBaseUrl(e.target.value)}
                  className="w-full border border-gray-200 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-50 transition-shadow font-mono text-xs"
                />
              </div>
              {editError && (
                <p className="text-sm text-red-600 bg-red-50 border border-red-100 rounded-xl px-4 py-2">{editError}</p>
              )}
              <div className="flex gap-3 pt-2">
                <button
                  onClick={() => setEditChannel(null)}
                  className="flex-1 border border-gray-200 text-gray-600 rounded-xl py-2.5 text-sm hover:bg-gray-50 transition-colors"
                >
                  取消
                </button>
                <button
                  onClick={handleEditSave}
                  disabled={editLoading}
                  className="flex-1 bg-gray-900 text-white rounded-xl py-2.5 text-sm hover:bg-gray-800 disabled:opacity-40 transition-colors"
                >
                  {editLoading ? '保存中...' : '保存'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
