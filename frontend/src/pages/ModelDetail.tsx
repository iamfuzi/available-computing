import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { modelsApi } from '../api/client'
import type { ModelRow, HealthRecord } from '../api/client'
import HealthBadge from '../components/HealthBadge'
import FreeTypeBadge from '../components/FreeTypeBadge'

const TABS = ['cURL', 'Python', 'Node.js'] as const
type Tab = typeof TABS[number]

function buildExample(m: ModelRow, tab: Tab): string {
  const base = m.base_url || ''
  const key = '••••[你的Key后4位]'
  if (tab === 'cURL') {
    return `curl ${base}/chat/completions \\
  -H "Authorization: Bearer ${key}" \\
  -H "Content-Type: application/json" \\
  -d '{"model":"${m.model_id}","messages":[{"role":"user","content":"Hello"}],"stream":true}'`
  }
  if (tab === 'Python') {
    return `from openai import OpenAI

client = OpenAI(api_key="${key}", base_url="${base}")

stream = client.chat.completions.create(
    model="${m.model_id}",
    messages=[{"role": "user", "content": "Hello"}],
    stream=True,
)
for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="")`
  }
  return `import OpenAI from "openai";

const client = new OpenAI({ apiKey: "${key}", baseURL: "${base}" });

const stream = await client.chat.completions.create({
  model: "${m.model_id}",
  messages: [{ role: "user", content: "Hello" }],
  stream: true,
});
for await (const chunk of stream) {
  process.stdout.write(chunk.choices[0]?.delta?.content ?? "");`
}

const STATUS_COLOR: Record<string, string> = {
  healthy: '#22c55e',
  slow: '#eab308',
  down: '#ef4444',
  unknown: '#9ca3af',
}

export default function ModelDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [model, setModel] = useState<ModelRow | null>(null)
  const [history, setHistory] = useState<HealthRecord[]>([])
  const [tab, setTab] = useState<Tab>('cURL')
  const [copied, setCopied] = useState(false)
  const [period, setPeriod] = useState<'24h' | '7d'>('24h')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!id) return
    setLoading(true)
    Promise.all([
      modelsApi.get(id),
      modelsApi.healthHistory(id, period),
    ]).then(([m, h]) => {
      setModel(m)
      setHistory(h)
    }).finally(() => setLoading(false))
  }, [id, period])

  if (loading && !model) return <div className="p-8 text-gray-400 text-sm animate-pulse">加载中...</div>
  if (!model) return null

  const rateLimit = model.rate_limit ? JSON.parse(model.rate_limit) : null

  function copyExample() {
    navigator.clipboard.writeText(buildExample(model!, tab))
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="max-w-3xl mx-auto px-4 py-6 space-y-4">
      <button
        onClick={() => navigate(-1)}
        className="text-sm text-gray-400 hover:text-gray-700 transition-colors"
      >
        ← 返回
      </button>

      {/* Header card */}
      <div className="bg-white border border-gray-200 rounded-2xl p-5 space-y-4 shadow-sm">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-lg font-bold text-gray-900 truncate">{model.model_id}</h1>
            <p className="text-sm text-gray-400 mt-0.5">
              {model.provider_name} · {model.category}
              {model.context_length ? ` · 上下文 ${Math.round(model.context_length / 1000)}K` : ''}
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <HealthBadge status={model.health_status} responseMs={model.last_response_ms} />
          </div>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <FreeTypeBadge freeType={model.free_type} source={model.free_source} />
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
          <div className="bg-gray-50 rounded-xl p-3 space-y-1.5">
            <div className="text-xs text-gray-400 font-medium uppercase tracking-wider">模型信息</div>
            <div className="text-gray-600">
              <span className="text-gray-400 text-xs">ID</span>{' '}
              <code className="bg-white px-1.5 py-0.5 rounded text-xs font-mono">{model.model_id}</code>
            </div>
            {model.base_url && (
              <div className="text-gray-600 truncate">
                <span className="text-gray-400 text-xs">Endpoint</span>{' '}
                <code className="bg-white px-1.5 py-0.5 rounded text-xs font-mono">{model.base_url}</code>
              </div>
            )}
            {model.free_source && (
              <div className="text-gray-600">
                <span className="text-gray-400 text-xs">免费判定</span> {model.free_source}
              </div>
            )}
          </div>
          <div className="bg-gray-50 rounded-xl p-3 space-y-1.5">
            <div className="text-xs text-gray-400 font-medium uppercase tracking-wider">速率限制</div>
            {rateLimit ? (
              <>
                <div className="text-gray-600">
                  {Object.entries(rateLimit).map(([k, v]) => (
                    <span key={k} className="inline-block mr-3">
                      <span className="text-gray-900 font-medium">{String(v)}</span>{' '}
                      <span className="text-gray-400 text-xs">{k.toUpperCase()}</span>
                    </span>
                  ))}
                </div>
                {model.rate_limit_source && (
                  <span className={`inline-block text-xs px-2 py-0.5 rounded-full ${
                    model.rate_limit_source === 'observed'
                      ? 'bg-green-50 text-green-600'
                      : 'bg-gray-100 text-gray-500'
                  }`}>
                    {model.rate_limit_source === 'observed' ? '实时采集' : '人工录入'}
                  </span>
                )}
                {model.rate_limit_updated_at && (
                  <span className="text-xs text-gray-400 ml-1">
                    {new Date(model.rate_limit_updated_at).toLocaleDateString()}
                  </span>
                )}
              </>
            ) : (
              <div className="text-gray-400 text-sm">暂无数据</div>
            )}
          </div>
        </div>
      </div>

      {/* Health history */}
      <div className="bg-white border border-gray-200 rounded-2xl p-5 space-y-3 shadow-sm">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-gray-900">健康历史</h2>
          <div className="flex bg-gray-100 rounded-lg p-0.5">
            {(['24h', '7d'] as const).map((p) => (
              <button
                key={p}
                onClick={() => setPeriod(p)}
                className={`text-xs px-3 py-1 rounded-md transition-colors ${
                  period === p ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500'
                }`}
              >
                {p}
              </button>
            ))}
          </div>
        </div>
        {history.length === 0 ? (
          <p className="text-sm text-gray-400 py-4 text-center">暂无健康记录，将在探测后生成</p>
        ) : (
          <div className="flex gap-[2px] h-12 items-end rounded-lg overflow-hidden bg-gray-50 p-1">
            {history.slice(-80).map((r, i) => (
              <div
                key={i}
                title={`${new Date(r.checked_at).toLocaleTimeString()}: ${r.status}${r.response_ms ? ` ${r.response_ms}ms` : ''}`}
                className="flex-1 rounded-sm transition-opacity hover:opacity-80"
                style={{
                  backgroundColor: STATUS_COLOR[r.status] ?? STATUS_COLOR.unknown,
                  height: r.response_ms
                    ? `${Math.max(8, Math.min(100, (r.response_ms / 3000) * 100))}%`
                    : '15%',
                }}
              />
            ))}
          </div>
        )}
        {history.length > 0 && (
          <div className="flex gap-4 text-xs text-gray-400">
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-green-500" /> 健康</span>
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-yellow-500" /> 慢</span>
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-red-500" /> 异常</span>
            <span className="ml-auto">{history.length} 条记录</span>
          </div>
        )}
      </div>

      {/* Code example */}
      <div className="bg-white border border-gray-200 rounded-2xl p-5 space-y-3 shadow-sm">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-gray-900">快速调用</h2>
          <button
            onClick={copyExample}
            className={`text-xs px-3 py-1.5 rounded-lg transition-colors ${
              copied
                ? 'bg-green-50 text-green-700'
                : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
            }`}
          >
            {copied ? '✓ 已复制' : '📋 复制'}
          </button>
        </div>
        <div className="flex bg-gray-900 rounded-lg p-0.5 gap-0.5">
          {TABS.map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`text-xs px-3 py-1.5 rounded-md transition-colors ${
                tab === t ? 'bg-gray-700 text-white' : 'text-gray-500'
              }`}
            >
              {t}
            </button>
          ))}
        </div>
        <pre className="bg-gray-950 text-gray-300 text-xs rounded-xl p-4 overflow-x-auto leading-relaxed whitespace-pre-wrap font-mono">
          {buildExample(model, tab)}
        </pre>
      </div>
    </div>
  )
}
