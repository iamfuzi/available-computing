import { useState, useEffect } from 'react'
import { apiKeysApi, modelsApi } from '../api/client'
import type { ApiKeyRow, ModelRow } from '../api/client'

type Tab = 'curl' | 'python' | 'node'

const TABS: { id: Tab; label: string }[] = [
  { id: 'curl', label: 'cURL' },
  { id: 'python', label: 'Python' },
  { id: 'node', label: 'Node.js' },
]

export default function ApiDocs() {
  const [keys, setKeys] = useState<ApiKeyRow[]>([])
  const [models, setModels] = useState<ModelRow[]>([])
  const [tab, setTab] = useState<Tab>('curl')
  const [copied, setCopied] = useState<string | null>(null)

  const baseUrl = `${window.location.origin}/v1`
  const activeKey = keys.find((k) => k.is_active)
  const keyDisplay = activeKey ? '你的 API 密钥' : '<your-api-key>'
  const sampleModel = models[0]?.model_id || 'auto:text'

  useEffect(() => {
    apiKeysApi.list().then(setKeys).catch(() => {})
    modelsApi.list({ free_only: true }).then(setModels).catch(() => {})
  }, [])

  function copy(text: string, id: string) {
    navigator.clipboard.writeText(text)
    setCopied(id)
    setTimeout(() => setCopied(null), 2000)
  }

  function chatExamples(model: string) {
    const curl = `curl ${baseUrl}/chat/completions \\
  -H "Authorization: Bearer ${keyDisplay}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "${model}",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'`

    const python = `from openai import OpenAI

client = OpenAI(
    base_url="${baseUrl}",
    api_key="${keyDisplay}"
)

response = client.chat.completions.create(
    model="${model}",
    messages=[{"role": "user", "content": "你好"}],
    stream=True
)
for chunk in response:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")`

    const node = `import OpenAI from 'openai';

const client = new OpenAI({
  baseURL: '${baseUrl}',
  apiKey: '${keyDisplay}',
});

const stream = await client.chat.completions.create({
  model: '${model}',
  messages: [{ role: 'user', content: '你好' }],
  stream: true,
});
for await (const chunk of stream) {
  process.stdout.write(chunk.choices[0]?.delta?.content || '');
}`

    return { curl, python, node }
  }

  function listExample() {
    const curl = `curl ${baseUrl}/models \\
  -H "Authorization: Bearer ${keyDisplay}"`
    const python = `from openai import OpenAI

client = OpenAI(base_url="${baseUrl}", api_key="${keyDisplay}")
models = client.models.list()
for m in models:
    print(m.id)`
    const node = `import OpenAI from 'openai';

const client = new OpenAI({ baseURL: '${baseUrl}', apiKey: '${keyDisplay}' });
const { data } = await client.models.list();
data.forEach(m => console.log(m.id));`
    return { curl, python, node }
  }

  function CodeBlock({ code, id }: { code: string; id: string }) {
    return (
      <div className="relative">
        <pre className="bg-gray-900 text-gray-100 rounded-xl p-4 pr-16 text-xs font-mono overflow-x-auto leading-relaxed">
          <code>{code}</code>
        </pre>
        <button
          onClick={() => copy(code, id)}
          className="absolute top-2 right-2 text-xs bg-gray-700 text-gray-300 px-2 py-1 rounded-lg hover:bg-gray-600 transition-colors"
        >
          {copied === id ? '✓' : '复制'}
        </button>
      </div>
    )
  }

  const chatCode = chatExamples(sampleModel)
  const autoCode = chatExamples('auto:text')
  const listCode = listExample()

  return (
    <div className="max-w-3xl mx-auto px-4 py-6 space-y-6">
      <div>
        <h1 className="text-lg font-bold text-gray-900">API 文档</h1>
        <p className="text-xs text-gray-400 mt-0.5">使用 OpenAI 兼容接口调用算力池</p>
      </div>

      {/* Quick Start */}
      <div className="bg-white border border-gray-200 rounded-2xl p-5 space-y-4 shadow-sm">
        <h2 className="text-sm font-semibold text-gray-900">快速开始</h2>

        <div>
          <label className="text-xs text-gray-500">Base URL</label>
          <div className="flex items-center gap-2 mt-1">
            <code className="flex-1 bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 text-sm font-mono select-all">
              {baseUrl}
            </code>
            <button
              onClick={() => copy(baseUrl, 'baseurl')}
              className="text-xs bg-blue-600 text-white rounded-lg px-4 py-2 hover:bg-blue-700 transition-colors shrink-0"
            >
              {copied === 'baseurl' ? '✓ 已复制' : '复制'}
            </button>
          </div>
        </div>

        <div>
          <label className="text-xs text-gray-500">认证方式</label>
          <p className="text-sm text-gray-700 mt-1">
            在请求头中添加 <code className="bg-gray-50 px-1.5 py-0.5 rounded text-xs">Authorization: Bearer &lt;api-key&gt;</code>。
            支持 <span className="font-medium">API 密钥</span>（以 <code className="text-xs">ac_</code> 开头）或管理员 JWT。
          </p>
        </div>

        {activeKey ? (
          <div>
            <label className="text-xs text-gray-500">当前密钥</label>
            <div className="flex items-center gap-2 mt-1">
              <code className="flex-1 bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 text-sm font-mono">
                {activeKey.key_prefix}
              </code>
              <a
                href="/settings"
                className="text-xs bg-blue-600 text-white rounded-lg px-4 py-2 hover:bg-blue-700 transition-colors shrink-0"
              >
                管理 API 密钥
              </a>
            </div>
          </div>
        ) : (
          <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 text-xs text-amber-700">
            你还没有创建 API 密钥。<a href="/settings" className="underline font-medium">前往设置页创建</a>，创建后记得复制保存。
          </div>
        )}
      </div>

      {/* Endpoints */}
      <div className="bg-white border border-gray-200 rounded-2xl p-5 space-y-5 shadow-sm">
        <h2 className="text-sm font-semibold text-gray-900">接口说明</h2>

        {/* Chat Completions */}
        <div className="space-y-3">
          <div>
            <code className="text-xs font-mono bg-blue-50 text-blue-700 px-2 py-0.5 rounded">POST</code>
            <code className="text-sm font-mono ml-2">/chat/completions</code>
          </div>
          <p className="text-sm text-gray-600">聊天补全，兼容 OpenAI Chat Completion API，支持流式 (SSE)。</p>

          <div className="flex gap-1">
            {TABS.map((t) => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={`text-xs px-3 py-1.5 rounded-lg transition-colors ${
                  tab === t.id ? 'bg-gray-900 text-white' : 'text-gray-500 hover:bg-gray-100'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
          <CodeBlock code={chatCode[tab]} id={`chat-${tab}`} />
        </div>

        {/* Models */}
        <div className="space-y-3 pt-4 border-t border-gray-100">
          <div>
            <code className="text-xs font-mono bg-green-50 text-green-700 px-2 py-0.5 rounded">GET</code>
            <code className="text-sm font-mono ml-2">/models</code>
          </div>
          <p className="text-sm text-gray-600">列出所有可用的免费模型。</p>
          <CodeBlock code={listCode[tab]} id={`list-${tab}`} />
        </div>
      </div>

      {/* Auto Routing */}
      <div className="bg-white border border-gray-200 rounded-2xl p-5 space-y-4 shadow-sm">
        <h2 className="text-sm font-semibold text-gray-900">智能路由</h2>
        <p className="text-sm text-gray-600">
          不知道用哪个模型？用 <code className="bg-gray-50 px-1.5 py-0.5 rounded text-xs">auto:text</code> 等前缀，系统自动选择当前最健康最快的模型。
        </p>
        <div className="flex flex-wrap gap-2">
          {[
            { prefix: 'auto:text', desc: '文本对话' },
            { prefix: 'auto:vision', desc: '多模态理解' },
            { prefix: 'auto:code', desc: '代码生成' },
          ].map((r) => (
            <div key={r.prefix} className="bg-gray-50 border border-gray-100 rounded-lg px-3 py-2">
              <code className="text-xs font-mono text-gray-900">{r.prefix}</code>
              <span className="text-xs text-gray-400 ml-2">{r.desc}</span>
            </div>
          ))}
        </div>
        <CodeBlock code={autoCode[tab]} id={`auto-${tab}`} />
      </div>

      {/* Available Models */}
      {models.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-2xl p-5 space-y-3 shadow-sm">
          <h2 className="text-sm font-semibold text-gray-900">当前可用模型 ({models.length})</h2>
          <div className="max-h-64 overflow-y-auto divide-y divide-gray-50">
            {models.slice(0, 30).map((m) => (
              <div key={m.id} className="flex items-center justify-between py-2">
                <code className="text-xs font-mono text-gray-900">{m.model_id}</code>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-gray-400">{m.provider_name}</span>
                  <span className={`w-1.5 h-1.5 rounded-full ${
                    m.health_status === 'healthy' ? 'bg-green-400' :
                    m.health_status === 'slow' ? 'bg-yellow-400' :
                    m.health_status === 'down' ? 'bg-red-400' : 'bg-gray-300'
                  }`} />
                </div>
              </div>
            ))}
            {models.length > 30 && (
              <p className="text-xs text-gray-400 py-2">…还有 {models.length - 30} 个模型</p>
            )}
          </div>
        </div>
      )}

      {/* Error Codes */}
      <div className="bg-white border border-gray-200 rounded-2xl p-5 space-y-3 shadow-sm">
        <h2 className="text-sm font-semibold text-gray-900">错误码</h2>
        <div className="space-y-2">
          {[
            { code: '401', desc: '未认证 — API 密钥无效或已过期' },
            { code: '404', desc: '模型不存在或当前不可用' },
            { code: '429', desc: '请求频率超限 — 降低调用频率或切换模型' },
            { code: '502', desc: '上游服务异常 — 系统会自动切换到其他可用节点' },
          ].map((e) => (
            <div key={e.code} className="flex items-start gap-3">
              <code className="text-xs font-mono bg-red-50 text-red-600 px-2 py-0.5 rounded shrink-0">{e.code}</code>
              <span className="text-sm text-gray-600">{e.desc}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
