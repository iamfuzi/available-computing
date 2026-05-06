import { useState, useEffect } from 'react'
import { channelsApi } from '../api/client'
import type { Provider } from '../api/client'

interface Props {
  open: boolean
  onClose: () => void
  onCreated: () => void
}

const PROVIDER_DOCS: Record<string, { hint: string; url: string }> = {
  groq: {
    hint: 'Groq Console → API Keys → Create Key',
    url: 'https://console.groq.com/keys',
  },
  siliconflow: {
    hint: '硅基流动控制台 → API 密钥 → 新建密钥',
    url: 'https://cloud.siliconflow.cn/account/ak',
  },
  gemini: {
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

  const doc = selectedProvider ? PROVIDER_DOCS[selectedProvider.id] : null

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
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md p-6">
        <div className="flex justify-between items-center mb-5">
          <h2 className="text-lg font-semibold text-gray-900">
            {step === 1 ? '添加厂商' : `添加厂商 — ${selectedProvider?.name}`}
          </h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">✕</button>
        </div>

        {step === 1 && (
          <div className="grid grid-cols-3 gap-3">
            {providers.map((p) => (
              <button
                key={p.id}
                className="border border-gray-200 rounded-xl p-3 text-left hover:border-blue-400 hover:bg-blue-50 transition-colors"
                onClick={() => { setSelectedProvider(p); setBaseUrl(p.base_url); setStep(2) }}
              >
                <div className="font-medium text-sm text-gray-900">{p.name}</div>
              </button>
            ))}
          </div>
        )}

        {step === 2 && selectedProvider && (
          <div className="space-y-4">
            {doc && (
              <div className="text-sm text-gray-500 bg-gray-50 rounded-lg p-3">
                📖 {doc.hint}
                <a href={doc.url} target="_blank" rel="noreferrer" className="ml-2 text-blue-600 hover:underline">
                  打开控制台 ↗
                </a>
              </div>
            )}

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">API Key *</label>
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="粘贴你的 API Key"
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-400"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Base URL <span className="text-gray-400 font-normal">（可选，留空使用官方地址）</span>
              </label>
              <input
                type="text"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder={selectedProvider.base_url}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-400"
              />
            </div>

            {error && <p className="text-sm text-red-600">{error}</p>}

            <div className="flex gap-3 pt-2">
              <button
                onClick={() => setStep(1)}
                className="flex-1 border border-gray-300 text-gray-700 rounded-lg py-2 text-sm hover:bg-gray-50"
              >
                返回
              </button>
              <button
                onClick={handleSubmit}
                disabled={loading || !apiKey.trim()}
                className="flex-1 bg-blue-600 text-white rounded-lg py-2 text-sm hover:bg-blue-700 disabled:opacity-50"
              >
                {loading ? '验证中...' : '验证并添加 →'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
