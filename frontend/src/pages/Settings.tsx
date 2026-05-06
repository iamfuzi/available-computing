import { useState, useEffect } from 'react'
import { settingsApi } from '../api/client'
import type { Settings as SettingsType } from '../api/client'

export default function SettingsPage() {
  const [settings, setSettings] = useState<SettingsType | null>(null)
  const [saved, setSaved] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    settingsApi.get().then((s) => { setSettings(s); setLoading(false) })
  }, [])

  if (loading) return <div className="p-8 text-gray-400 text-sm animate-pulse">加载中...</div>
  if (!settings) return null

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
    <div className="max-w-2xl mx-auto px-4 py-6 space-y-5">
      <div>
        <h1 className="text-lg font-bold text-gray-900">设置</h1>
        <p className="text-xs text-gray-400 mt-0.5">系统参数和偏好</p>
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
        Available Computing v0.1.0 · <a href="https://github.com/iamfuzi/available-computing" target="_blank" rel="noreferrer" className="hover:text-gray-500">GitHub</a>
      </div>
    </div>
  )
}
