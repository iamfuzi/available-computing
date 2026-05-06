import { useState, useEffect } from 'react'
import { settingsApi } from '../api/client'
import type { Settings } from '../api/client'

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => { settingsApi.get().then(setSettings) }, [])

  if (!settings) return <div className="p-8 text-gray-400">加载中...</div>

  async function handleSave() {
    await settingsApi.update({
      discovery_interval_hours: settings!.discovery_interval_hours,
      probe_interval_hours: settings!.probe_interval_hours,
      slow_threshold_ms: settings!.slow_threshold_ms,
    })
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-2xl mx-auto px-4 py-6 space-y-5">
        <h1 className="text-xl font-semibold text-gray-900">⚙️ 设置</h1>

        <div className="bg-white border border-gray-200 rounded-2xl p-6 space-y-5">
          <h2 className="font-medium text-gray-900">探测设置</h2>

          <label className="flex items-center justify-between">
            <span className="text-sm text-gray-700">自动刷新频率（小时）</span>
            <input
              type="number" min={1} max={24}
              value={settings.discovery_interval_hours}
              onChange={(e) => setSettings({ ...settings, discovery_interval_hours: e.target.value })}
              className="border border-gray-300 rounded px-2 py-1 text-sm w-20 text-right"
            />
          </label>

          <label className="flex items-center justify-between">
            <span className="text-sm text-gray-700">主动探测频率（小时）</span>
            <input
              type="number" min={1} max={24}
              value={settings.probe_interval_hours}
              onChange={(e) => setSettings({ ...settings, probe_interval_hours: e.target.value })}
              className="border border-gray-300 rounded px-2 py-1 text-sm w-20 text-right"
            />
          </label>

          <label className="flex items-center justify-between">
            <span className="text-sm text-gray-700">慢速阈值（ms）</span>
            <input
              type="number" min={100}
              value={settings.slow_threshold_ms}
              onChange={(e) => setSettings({ ...settings, slow_threshold_ms: e.target.value })}
              className="border border-gray-300 rounded px-2 py-1 text-sm w-20 text-right"
            />
          </label>
        </div>

        <div className="bg-white border border-gray-200 rounded-2xl p-6 space-y-3">
          <h2 className="font-medium text-gray-900">白名单管理</h2>
          <p className="text-sm text-gray-500">当前版本：{settings.whitelist_version}</p>
        </div>

        <div className="bg-white border border-gray-200 rounded-2xl p-6 space-y-3">
          <h2 className="font-medium text-gray-900">访问安全</h2>
          <div className="flex items-center justify-between text-sm">
            <span className="text-gray-700">Key 加密</span>
            <span className="text-green-700 bg-green-50 border border-green-200 px-2 py-0.5 rounded text-xs">始终开启</span>
          </div>
        </div>

        <button
          onClick={handleSave}
          className="w-full bg-blue-600 text-white rounded-xl py-2.5 text-sm font-medium hover:bg-blue-700"
        >
          {saved ? '✓ 已保存' : '保存设置'}
        </button>
      </div>
    </div>
  )
}
