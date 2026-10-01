import { useState, useEffect, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { modelsApi } from '../api/client'
import type { ModelRow, HealthRecord, RequestLogRow } from '../api/client'
import HealthBadge from '../components/HealthBadge'
import FreshnessBadge from '../components/FreshnessBadge'
import FreeTypeBadge from '../components/FreeTypeBadge'

const TABS = ['cURL', 'Python', 'Node.js'] as const
type Tab = typeof TABS[number]
type FreeTypeChoice = 'permanent' | 'quota' | 'grant'

const FREE_TYPE_OPTIONS: { value: FreeTypeChoice; label: string }[] = [
  { value: 'permanent', label: '永久免费' },
  { value: 'quota', label: '免费配额（每日/月上限）' },
  { value: 'grant', label: '新用户赠送' },
]

const FREE_SOURCE_LABELS: Record<string, string> = {
  manual: '人工裁定',
  whitelist: '白名单',
  prefix_rule: '前缀规则（Pro/LoRA 等收费家族）',
  provider_free: '厂商整体免费',
  api_field: 'API 字段',
  api_free_set: '厂商免费模型列表',
  event_recheck: '自动复检',
  probe_restored: '探测恢复',
}

function FreeReviewCard({ model, onUpdated }: { model: ModelRow; onUpdated: (m: ModelRow) => void }) {
  const [busy, setBusy] = useState<'paid' | 'free' | null>(null)
  const [error, setError] = useState('')
  const [done, setDone] = useState('')
  const [freeType, setFreeType] = useState<FreeTypeChoice>('permanent')

  const needsReview = model.is_free == null || model.free_type === 'billing_suspect'

  async function submit(decision: 'paid' | 'free') {
    setBusy(decision)
    setError('')
    setDone('')
    try {
      const updated = await modelsApi.review(model.id, decision, decision === 'free' ? freeType : undefined)
      onUpdated(updated)
      setDone(decision === 'paid' ? '已标记为收费模型，并移出免费池' : '已确认免费，恢复参与路由')
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作失败')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className={`rounded-2xl p-5 space-y-3 shadow-sm border ${
      needsReview
        ? 'bg-orange-50 border-orange-300'
        : 'bg-white border-gray-200'
    }`}>
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-gray-900">免费状态 · 人工裁定</h2>
        <FreeTypeBadge freeType={model.free_type} source={model.free_source} isFree={model.is_free} />
      </div>

      {needsReview && (
        <p className="text-sm text-orange-800">
          该模型出现疑似计费信号，等待人工确认。确认为收费后将从免费池移出（不再路由、不再探测）；确认免费则恢复参与路由。裁定结果不会被自动发现覆盖。
        </p>
      )}
      {!needsReview && (
        <p className="text-sm text-gray-500">
          如厂商计费策略与展示不符，可在此人工改判。人工裁定优先于白名单和自动发现。
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {model.is_free !== true && (
          <>
            <select
              value={freeType}
              onChange={(e) => setFreeType(e.target.value as FreeTypeChoice)}
              disabled={busy !== null}
              className="border border-gray-200 rounded-lg px-2 py-2 text-sm bg-white focus:outline-none focus:border-blue-400 disabled:opacity-50"
            >
              {FREE_TYPE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
            <button
              onClick={() => submit('free')}
              disabled={busy !== null}
              className="bg-green-600 text-white text-sm px-4 py-2 rounded-lg hover:bg-green-700 disabled:opacity-50 transition-colors"
            >
              {busy === 'free' ? '...' : '确认免费'}
            </button>
          </>
        )}
        {model.is_free !== false && (
          <button
            onClick={() => submit('paid')}
            disabled={busy !== null}
            className="bg-red-600 text-white text-sm px-4 py-2 rounded-lg hover:bg-red-700 disabled:opacity-50 transition-colors"
          >
            {busy === 'paid' ? '...' : '标记为收费'}
          </button>
        )}
      </div>

      {done && <p className="text-sm text-green-700">{done}</p>}
      {error && <p className="text-sm text-red-600">{error}</p>}
    </div>
  )
}

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

function QuickTry({ modelId, providerName }: { modelId: string; providerName: string }) {
  const [input, setInput] = useState('')
  const [response, setResponse] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)

  async function handleSend() {
    const msg = input.trim()
    if (!msg || loading) return
    setLoading(true)
    setError('')
    setResponse('')
    setInput('')

    try {
      const token = localStorage.getItem('token')
      const res = await fetch('/v1/chat/completions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify({
          model: modelId,
          messages: [{ role: 'user', content: msg }],
          stream: true,
        }),
      })

      if (!res.ok) {
        const err = await res.json().catch(() => ({ error: { message: res.statusText } }))
        throw new Error(err.error?.message || err.detail || `HTTP ${res.status}`)
      }

      const reader = res.body?.getReader()
      if (!reader) throw new Error('No response body')
      const decoder = new TextDecoder()
      let text = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        const chunk = decoder.decode(value, { stream: true })
        for (const line of chunk.split('\n')) {
          if (!line.startsWith('data: ') || line === 'data: [DONE]') continue
          try {
            const data = JSON.parse(line.slice(6))
            const delta = data.choices?.[0]?.delta?.content
            if (delta) {
              text += delta
              setResponse(text)
            }
          } catch { /* skip malformed lines */ }
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : '调用失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [response])

  return (
    <div className="bg-white border border-gray-200 rounded-2xl p-5 space-y-3 shadow-sm">
      <h2 className="text-sm font-semibold text-gray-900">试一试 · {providerName}</h2>

      {(response || error) && (
        <div className="bg-gray-50 rounded-xl p-4 text-sm whitespace-pre-wrap break-words max-h-80 overflow-y-auto">
          {response && <span className="text-gray-800">{response}</span>}
          {error && <span className="text-red-600">{error}</span>}
          <div ref={bottomRef} />
        </div>
      )}

      <div className="flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() } }}
          placeholder="输入消息，按 Enter 发送..."
          disabled={loading}
          className="flex-1 border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-100 disabled:opacity-50 transition-shadow"
        />
        <button
          onClick={handleSend}
          disabled={loading || !input.trim()}
          className="bg-gray-900 text-white text-sm px-4 py-2 rounded-lg hover:bg-gray-800 disabled:opacity-50 transition-colors shrink-0"
        >
          {loading ? '...' : '发送'}
        </button>
      </div>
    </div>
  )
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
  const [reqLogs, setReqLogs] = useState<RequestLogRow[]>([])
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
      modelsApi.requestLogs(id).catch(() => [] as RequestLogRow[]),
    ]).then(([m, h, rl]) => {
      setModel(m)
      setHistory(h)
      setReqLogs(rl)
    }).catch(() => {
      // model stays null, will show blank state
    }).finally(() => setLoading(false))
  }, [id, period])

  if (loading && !model) return <div className="p-8 text-gray-400 text-sm animate-pulse">加载中...</div>
  if (!model) return null

  const rateLimit = (() => {
    try {
      return model.rate_limit ? JSON.parse(model.rate_limit) : null
    } catch {
      return null
    }
  })()

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
              {model.param_size != null ? ` · 参数量 ${model.param_size}B` : ''}
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <HealthBadge status={model.health_status} responseMs={model.last_response_ms} />
            <FreshnessBadge
              lastVerifiedAt={model.last_verified_at}
              thresholdDays={model.staleness_threshold_days}
              method={model.verification_method}
            />
          </div>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <FreeTypeBadge freeType={model.free_type} source={model.free_source} isFree={model.is_free} />
          {model.free_expires_at && (
            <span className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-full px-2 py-0.5">
              免费至 {new Date(model.free_expires_at).toLocaleDateString()}
            </span>
          )}
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
                <span className="text-gray-400 text-xs">免费判定</span>{' '}
                {FREE_SOURCE_LABELS[model.free_source] ?? model.free_source}
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

      {/* Manual billing adjudication */}
      <FreeReviewCard model={model} onUpdated={setModel} />

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
                title={`${new Date(r.checked_at).toLocaleString()}: ${r.status}${r.failure_reason ? `（${r.failure_reason}）` : ''}${r.response_ms ? ` ${r.response_ms}ms` : ''}${r.is_passive ? ' · 真实流量' : ' · 探测'}`}
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
        {history.length > 0 && (() => {
          const reasons = history.filter((r) => r.failure_reason).reduce<Record<string, number>>((acc, r) => {
            acc[r.failure_reason!] = (acc[r.failure_reason!] || 0) + 1
            return acc
          }, {})
          const reasonEntries = Object.entries(reasons).sort((a, b) => b[1] - a[1]).slice(0, 4)
          const passive = history.filter((r) => r.is_passive).length
          return (
            <div className="space-y-1.5">
              <div className="flex gap-4 text-xs text-gray-400">
                <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-green-500" /> 健康</span>
                <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-yellow-500" /> 响应偏慢</span>
                <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-red-500" /> 异常</span>
                <span className="ml-auto">{history.length} 条 · 真实流量 {passive} · 探测 {history.length - passive}</span>
              </div>
              {reasonEntries.length > 0 && (
                <div className="flex flex-wrap gap-1.5 text-xs text-gray-500">
                  <span className="text-gray-400">失败归因：</span>
                  {reasonEntries.map(([reason, n]) => (
                    <span key={reason} className="bg-gray-100 rounded-full px-2 py-0.5" title="悬停健康历史柱条可见每次记录的归因">
                      {reason} ×{n}
                    </span>
                  ))}
                </div>
              )}
            </div>
          )
        })()}
      </div>

      {/* Recent terminal requests */}
      {reqLogs.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-2xl p-5 space-y-3 shadow-sm">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-gray-900">最近请求</h2>
            <span className="text-xs text-gray-400">保留 7 天 · 重建容器不丢</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-gray-400">
                <tr>
                  <th className="text-left font-medium py-1.5">时间</th>
                  <th className="text-left font-medium py-1.5">请求模型</th>
                  <th className="text-left font-medium py-1.5">结果</th>
                  <th className="text-left font-medium py-1.5">延迟</th>
                  <th className="text-left font-medium py-1.5 hidden sm:table-cell">换道链</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {reqLogs.map((r, i) => (
                  <tr key={i}>
                    <td className="py-1.5 text-gray-500 whitespace-nowrap">{new Date(r.ts).toLocaleString()}</td>
                    <td className="py-1.5 font-mono text-gray-700">{r.requested_model}</td>
                    <td className="py-1.5">
                      {r.outcome === 'success' ? (
                        <span className="text-green-600">成功 {r.status_code}</span>
                      ) : (
                        <span className="text-red-500" title={r.error_code || ''}>
                          失败 {r.status_code}{r.error_code ? ` · ${r.error_code}` : ''}
                        </span>
                      )}
                    </td>
                    <td className="py-1.5 text-gray-500">{r.latency_ms != null ? `${r.latency_ms}ms` : '—'}</td>
                    <td className="py-1.5 text-gray-400 hidden sm:table-cell max-w-[220px] truncate" title={r.attempted || ''}>
                      {r.attempted || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Quick try */}
      <QuickTry modelId={model.model_id} providerName={model.provider_name || ''} />

      {/* Code example */}
      <details className="bg-white border border-gray-200 rounded-2xl shadow-sm">
        <summary className="px-5 py-4 cursor-pointer text-sm font-semibold text-gray-900 select-none">
          代码示例
        </summary>
        <div className="px-5 pb-5 space-y-3">
          <div className="flex items-center justify-between">
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
          <pre className="bg-gray-950 text-gray-300 text-xs rounded-xl p-4 overflow-x-auto leading-relaxed whitespace-pre-wrap font-mono">
            {buildExample(model, tab)}
          </pre>
        </div>
      </details>
    </div>
  )
}
