import { useState, useEffect } from 'react'
import { channelsApi } from '../api/client'
import type { Provider } from '../api/client'

interface Props {
  open: boolean
  onClose: () => void
  onCreated: () => void
}

const PROVIDER_INFO: Record<string, { hint: string; url: string; desc: string }> = {
  groq: {
    desc: '全部模型永久免费',
    hint: 'Groq Console → API Keys → Create Key',
    url: 'https://console.groq.com/keys',
  },
  siliconflow: {
    desc: '部分模型永久免费',
    hint: '硅基流动控制台 → API 密钥 → 新建密钥',
    url: 'https://cloud.siliconflow.cn/account/ak',
  },
  gemini: {
    desc: '每日 1500 次免费额度',
    hint: 'Google AI Studio → Get API key',
    url: 'https://aistudio.google.com/app/apikey',
  },
}

export default function AddChannelModal({ open, onClose, onCreated }: Props) {
  const [providers, setProviders] = useState<Provider[]>([])
  const [step, setStep] = useState<1 | 2>(1)
  const [selectedProvider, setSelectedProvider] = useState<Provider | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (open) {
      channelsApi.providers().then(setProviders)
      setStep(1)
      setSelectedProvider(null)
      setApiKey('')
      setBaseUrl('')
      setError('')
    }
  }, [open])

  if (!open) return null

  const info = selectedProvider ? PROVIDER_INFO[selectedProvider.id] : null

  async function handleSubmit() {
    if (!selectedProvider || !apiKey.trim()) return
    setLoading(true)
    setError('')
    try {
      await channelsApi.create({
        provider_type: selectedProvider.id,
        api_key: apiKey.trim(),
        base_url: baseUrl.trim() || undefined,
      })
      onCreated()
      onClose()
    } catch (e: any) {
      setError(e.response?.data?.detail ?? '添加失败，请检查 Key 是否正确')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md">
        {/* Header */}
        <div className="flex justify-between items-center px-6 py-4 border-b border-gray-100">
          <h2 className="text-base font-semibold text-gray-900">
            {step === 1 ? '选择厂商' : `添加 ${selectedProvider?.name}`}
          </h2>
          <button onClick={onClose} className="text-gray-300 hover:text-gray-600 text-lg leading-none transition-colors">
            ✕
          </button>
        </div>

        <div className="p-6">
          {step === 1 && (
            <div className="grid gap-3">
              {providers.map((p) => {
                const pInfo = PROVIDER_INFO[p.id]
                return (
                  <button
                    key={p.id}
                    className="border border-gray-200 rounded-xl p-4 text-left hover:border-blue-400 hover:bg-blue-50/50 transition-all group"
                    onClick={() => { setSelectedProvider(p); setBaseUrl(p.base_url); setStep(2) }}
                  >
                    <div className="font-medium text-gray-900 group-hover:text-blue-700">{p.name}</div>
                    {pInfo && <div className="text-xs text-gray-400 mt-0.5">{pInfo.desc}</div>}
                  </button>
                )
              })}
              <button
                onClick={() => window.open('https://github.com/iamfuzi/available-computing/issues', '_blank')}
                className="border border-dashed border-gray-300 rounded-xl p-3 text-sm text-gray-400 hover:text-gray-600 hover:border-gray-400 transition-colors"
              >
                没找到？请求支持新厂商 →
              </button>
            </div>
          )}

          {step === 2 && selectedProvider && (
            <div className="space-y-4">
              {info && (
                <div className="text-sm text-gray-500 bg-blue-50 border border-blue-100 rounded-xl p-3">
                  <div className="font-medium text-blue-800 text-xs mb-1">📖 获取 Key</div>
                  <div>{info.hint}</div>
                  <a
                    href={info.url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-blue-600 hover:text-blue-800 text-xs mt-1 inline-block"
                  >
                    打开控制台 ↗
                  </a>
                </div>
              )}

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1.5">API Key *</label>
                <input
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="粘贴你的 API Key"
                  className="w-full border border-gray-200 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-50 transition-shadow font-mono"
                  autoFocus
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1.5">
                  Base URL <span className="text-gray-400 font-normal text-xs">（可选）</span>
                </label>
                <input
                  type="text"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder={selectedProvider.base_url}
                  className="w-full border border-gray-200 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-50 transition-shadow font-mono text-xs"
                />
              </div>

              {error && (
                <p className="text-sm text-red-600 bg-red-50 border border-red-100 rounded-xl px-4 py-2">{error}</p>
              )}

              <div className="flex gap-3 pt-2">
                <button
                  onClick={() => setStep(1)}
                  className="flex-1 border border-gray-200 text-gray-600 rounded-xl py-2.5 text-sm hover:bg-gray-50 transition-colors"
                >
                  ← 返回
                </button>
                <button
                  onClick={handleSubmit}
                  disabled={loading || !apiKey.trim()}
                  className="flex-1 bg-gray-900 text-white rounded-xl py-2.5 text-sm hover:bg-gray-800 disabled:opacity-40 transition-colors"
                >
                  {loading ? '验证中...' : '验证并添加 →'}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
