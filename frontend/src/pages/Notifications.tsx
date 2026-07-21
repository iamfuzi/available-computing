import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { notificationsApi } from '../api/client'
import type { NotificationRow } from '../api/client'
import { useWebSocket } from '../hooks/useWebSocket'

const severityStyle = {
  critical: 'border-red-200 bg-red-50 text-red-900',
  warning: 'border-amber-200 bg-amber-50 text-amber-900',
  info: 'border-blue-200 bg-blue-50 text-blue-900',
}

export default function Notifications() {
  const [items, setItems] = useState<NotificationRow[]>([])
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()

  const load = useCallback(async () => {
    try {
      setItems(await notificationsApi.list())
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])
  useWebSocket(() => { load() })

  async function open(item: NotificationRow) {
    if (item.status === 'unread') await notificationsApi.update(item.id, 'read')
    if (item.action_path) navigate(item.action_path)
    else load()
  }

  async function dismiss(item: NotificationRow) {
    await notificationsApi.update(item.id, 'dismissed')
    load()
  }

  async function readAll() {
    await notificationsApi.readAll()
    load()
  }

  return (
    <div className="max-w-4xl mx-auto px-4 py-6 space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-gray-900">消息中心</h1>
          <p className="text-xs text-gray-400 mt-0.5">仅展示仍需处理的厂商、候选池和免费策略事件</p>
        </div>
        <button onClick={readAll} className="text-sm text-blue-600 hover:text-blue-700">全部标为已读</button>
      </div>

      {loading ? (
        <div className="text-sm text-gray-400 py-12 text-center">加载中...</div>
      ) : items.length === 0 ? (
        <div className="bg-white border border-gray-200 rounded-2xl p-12 text-center text-sm text-gray-400">
          当前没有需要处理的通知
        </div>
      ) : (
        <div className="space-y-3">
          {items.map((item) => (
            <div key={item.id} className={`border rounded-2xl p-4 ${severityStyle[item.severity]}`}>
              <div className="flex gap-3">
                <span className={`mt-1 w-2 h-2 rounded-full shrink-0 ${item.status === 'unread' ? 'bg-current' : 'bg-gray-300'}`} />
                <div className="min-w-0 flex-1">
                  <div className="font-medium text-sm">{item.title}</div>
                  <p className="text-xs opacity-75 mt-1 break-words">{item.message}</p>
                  <div className="flex gap-3 mt-3 text-xs">
                    {item.action_path && <button onClick={() => open(item)} className="font-medium underline underline-offset-2">查看并处理</button>}
                    <button onClick={() => dismiss(item)} className="opacity-60 hover:opacity-100">忽略</button>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
