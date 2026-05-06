import { useState, useEffect } from 'react'
import { channelsApi } from '../api/client'
import type { Channel } from '../api/client'
import AddChannelModal from '../components/AddChannelModal'

export default function Channels() {
  const [channels, setChannels] = useState<Channel[]>([])
  const [showAddModal, setShowAddModal] = useState(false)
  const [probingId, setProbingId] = useState<string | null>(null)

  async function load() {
    setChannels(await channelsApi.list())
  }

  useEffect(() => { load() }, [])

  async function handleProbe(id: string) {
    setProbingId(id)
    await channelsApi.probe(id)
    setTimeout(() => { setProbingId(null); load() }, 2000)
  }

  async function handleToggle(ch: Channel) {
    await channelsApi.update(ch.id, { enabled: !ch.enabled })
    load()
  }

  async function handleDelete(id: string) {
    if (!confirm('确认删除该厂商及其所有模型数据？')) return
    await channelsApi.delete(id)
    load()
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-4xl mx-auto px-4 py-6 space-y-4">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-semibold text-gray-900">🔌 厂商管理</h1>
          <button
            onClick={() => setShowAddModal(true)}
            className="bg-blue-600 text-white text-sm px-4 py-2 rounded-lg hover:bg-blue-700"
          >
            ＋ 添加厂商
          </button>
        </div>

        {channels.length === 0 && (
          <div className="bg-white border border-gray-200 rounded-2xl p-10 text-center text-gray-400">
            还没有接入任何厂商
          </div>
        )}

        {channels.map((ch) => (
          <div key={ch.id} className={`bg-white border rounded-xl p-4 space-y-2 ${ch.enabled ? 'border-gray-200' : 'border-gray-100 opacity-60'}`}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full ${ch.enabled ? 'bg-green-500' : 'bg-gray-300'}`} />
                <span className="font-medium text-gray-900">{ch.name}</span>
                <span className="text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded">{ch.provider_type}</span>
              </div>
              <span className="text-sm text-blue-700 font-medium">
                {ch.free_model_count > 0 ? `🟢 ${ch.free_model_count} 个免费模型` : '探测中...'}
              </span>
            </div>

            <div className="flex items-center justify-between text-sm text-gray-500">
              <span>API Key: {ch.api_key_hint}</span>
              <span className="text-xs">
                {ch.last_probed_at
                  ? `最后探测: ${new Date(ch.last_probed_at).toLocaleString()}`
                  : '尚未探测'}
              </span>
            </div>

            <div className="flex gap-2 pt-1">
              <button
                onClick={() => handleProbe(ch.id)}
                disabled={probingId === ch.id}
                className="text-xs border border-gray-200 px-3 py-1 rounded-lg hover:bg-gray-50 disabled:opacity-50"
              >
                {probingId === ch.id ? '探测中...' : '刷新'}
              </button>
              <button
                onClick={() => handleToggle(ch)}
                className="text-xs border border-gray-200 px-3 py-1 rounded-lg hover:bg-gray-50"
              >
                {ch.enabled ? '禁用' : '启用'}
              </button>
              <button
                onClick={() => handleDelete(ch.id)}
                className="text-xs border border-red-200 text-red-600 px-3 py-1 rounded-lg hover:bg-red-50"
              >
                删除
              </button>
            </div>
          </div>
        ))}
      </div>

      <AddChannelModal
        open={showAddModal}
        onClose={() => setShowAddModal(false)}
        onCreated={load}
      />
    </div>
  )
}
