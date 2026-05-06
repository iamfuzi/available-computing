import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { modelsApi } from '../api/client'
import type { ModelRow, HealthRecord } from '../api/client'
import HealthBadge from '../components/HealthBadge'
import FreeTypeBadge from '../components/FreeTypeBadge'

const TABS = ['cURL', 'Python', 'Node.js']

function buildExample(m: ModelRow, tab: string): string {
  const base = m.base_url || ''
  const key = m.provider_name ? '••••[你的Key后4位]' : '<your-key>'
  if (tab === 'cURL') {
    return `curl ${base}/chat/completions \\\n  -H "Authorization: Bearer ${key}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model":"${m.model_id}","messages":[{"role":"user","content":"Hello"}],"stream":true}'`
  }
  if (tab === 'Python') {
    return `from openai import OpenAI\n\nclient = OpenAI(\n    api_key="${key}",\n    base_url="${base}",\n)\n\nstream = client.chat.completions.create(\n    model="${m.model_id}",\n    messages=[{"role":"user","content":"Hello"}],\n    stream=True,\n)\nfor chunk in stream:\n    print(chunk.choices[0].delta.content or "", end="")`
  }
  return `import OpenAI from "openai";\n\nconst client = new OpenAI({\n  apiKey: "${key}",\n  baseURL: "${base}",\n});\n\nconst stream = await client.chat.completions.create({\n  model: "${m.model_id}",\n  messages: [{ role: "user", content: "Hello" }],\n  stream: true,\n});\nfor await (const chunk of stream) {\n  process.stdout.write(chunk.choices[0]?.delta?.content ?? "");\n}`
}

export default function ModelDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [model, setModel] = useState<ModelRow | null>(null)
  const [history, setHistory] = useState<HealthRecord[]>([])
  const [tab, setTab] = useState('cURL')
  const [copied, setCopied] = useState(false)
  const [period, setPeriod] = useState<'24h' | '7d'>('24h')

  useEffect(() => {
    if (!id) return
    modelsApi.get(id).then(setModel)
    modelsApi.healthHistory(id, period).then(setHistory)
  }, [id, period])

  if (!model) return <div className="p-8 text-gray-400">加载中...</div>

  const rateLimit = model.rate_limit ? JSON.parse(model.rate_limit) : null

  function copyExample() {
    navigator.clipboard.writeText(buildExample(model!, tab))
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-3xl mx-auto px-4 py-6 space-y-5">
        <button onClick={() => navigate(-1)} className="text-sm text-gray-500 hover:text-gray-700">← 返回</button>

        <div className="bg-white border border-gray-200 rounded-2xl p-6 space-y-4">
          <div>
            <h1 className="text-xl font-semibold text-gray-900">{model.model_id}</h1>
            <p className="text-sm text-gray-500 mt-1">
              来自 {model.provider_name} · {model.category} ·{' '}
              {model.context_length ? `上下文 ${Math.round(model.context_length / 1000)}K` : ''}
            </p>
          </div>

          <div className="flex items-center gap-3 flex-wrap">
            <HealthBadge status={model.health_status} responseMs={model.last_response_ms} />
            <FreeTypeBadge freeType={model.free_type} source={model.free_source} />
          </div>

          <div className="text-sm space-y-1 text-gray-600">
            <div><span className="text-gray-400">模型 ID:</span> <code className="bg-gray-100 px-1.5 py-0.5 rounded text-xs">{model.model_id}</code></div>
            <div><span className="text-gray-400">Endpoint:</span> <code className="bg-gray-100 px-1.5 py-0.5 rounded text-xs">{model.base_url}</code></div>
            {rateLimit && (
              <div>
                <span className="text-gray-400">速率限制:</span>{' '}
                {Object.entries(rateLimit).map(([k, v]) => `${v} ${k.toUpperCase()}`).join(' / ')}
                {model.rate_limit_source && (
                  <span className={`ml-2 text-xs px-1.5 py-0.5 rounded ${
                    model.rate_limit_source === 'observed'
                      ? 'bg-green-50 text-green-700'
                      : 'bg-gray-100 text-gray-500'
                  }`}>
                    {model.rate_limit_source === 'observed' ? '实时采集' : '人工录入'}
                  </span>
                )}
                {model.rate_limit_updated_at && (
                  <span className="ml-1 text-xs text-gray-400">
                    更新于 {new Date(model.rate_limit_updated_at).toLocaleDateString()}
                  </span>
                )}
              </div>
            )}
            {model.free_source && (
              <div><span className="text-gray-400">免费判定来源:</span> {model.free_source}</div>
            )}
          </div>
        </div>

        {/* Health history */}
        <div className="bg-white border border-gray-200 rounded-2xl p-6 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="font-medium text-gray-900">📈 健康历史</h2>
            <div className="flex gap-1">
              {(['24h', '7d'] as const).map((p) => (
                <button
                  key={p}
                  onClick={() => setPeriod(p)}
                  className={`text-xs px-3 py-1 rounded ${period === p ? 'bg-blue-600 text-white' : 'border border-gray-200 text-gray-600'}`}
                >
                  {p}
                </button>
              ))}
            </div>
          </div>
          {history.length === 0 ? (
            <p className="text-sm text-gray-400">暂无健康记录</p>
          ) : (
            <div className="flex gap-0.5 h-10 items-end">
              {history.slice(-60).map((r, i) => (
                <div
                  key={i}
                  title={`${r.checked_at}: ${r.status} ${r.response_ms ? r.response_ms + 'ms' : ''}`}
                  className={`flex-1 rounded-sm ${
                    r.status === 'healthy' ? 'bg-green-400' :
                    r.status === 'slow' ? 'bg-yellow-400' :
                    r.status === 'down' ? 'bg-red-400' : 'bg-gray-200'
                  }`}
                  style={{ height: r.response_ms ? `${Math.min(100, (r.response_ms / 3000) * 100)}%` : '20%' }}
                />
              ))}
            </div>
          )}
        </div>

        {/* Code example */}
        <div className="bg-white border border-gray-200 rounded-2xl p-6 space-y-3">
          <h2 className="font-medium text-gray-900">🚀 快速调用</h2>
          <div className="flex gap-1">
            {TABS.map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`text-xs px-3 py-1 rounded ${tab === t ? 'bg-blue-600 text-white' : 'border border-gray-200 text-gray-600'}`}
              >
                {t}
              </button>
            ))}
          </div>
          <pre className="bg-gray-950 text-gray-100 text-xs rounded-xl p-4 overflow-x-auto leading-relaxed whitespace-pre-wrap">
            {buildExample(model, tab)}
          </pre>
          <button
            onClick={copyExample}
            className="text-sm text-blue-600 hover:text-blue-800"
          >
            {copied ? '✓ 已复制' : '📋 复制（含完整 Key）'}
          </button>
        </div>
      </div>
    </div>
  )
}
