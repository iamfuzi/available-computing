import { useState, useEffect } from 'react'
import { apiKeysApi, modelsApi, channelsApi } from '../api/client'
import type { ApiKeyRow, ModelRow, Channel } from '../api/client'

type Tab = 'test' | 'curl' | 'python' | 'node'
type CodeTab = 'curl' | 'python' | 'node'

const CODE_TABS: { id: CodeTab; label: string }[] = [
  { id: 'curl', label: 'cURL' },
  { id: 'python', label: 'Python' },
  { id: 'node', label: 'Node.js' },
]

const TEST_TABS = [
  { id: 'test' as Tab, label: '🧪 在线测试' },
  { id: 'curl' as Tab, label: 'cURL' },
  { id: 'python' as Tab, label: 'Python' },
  { id: 'node' as Tab, label: 'Node.js' },
]

export default function ApiDocs() {
  const [keys, setKeys] = useState<ApiKeyRow[]>([])
  const [channels, setChannels] = useState<Channel[]>([])
  const [models, setModels] = useState<ModelRow[]>([])
  const [tab, setTab] = useState<Tab>('test')
  const [codeTab, setCodeTab] = useState<CodeTab>('curl')
  const [copied, setCopied] = useState<string | null>(null)

  // Test panel states
  const [selectedChannelId, setSelectedChannelId] = useState<string>('')
  const [selectedModel, setSelectedModel] = useState<string>('')
  const [selectedKeyId, setSelectedKeyId] = useState<string>('')
  const [testMessage, setTestMessage] = useState('你好，介绍一下你自己')
  const [testResponse, setTestResponse] = useState<string>('')
  const [isStreaming, setIsStreaming] = useState(false)
  const [streamFinished, setStreamFinished] = useState(true)
  const [testError, setTestError] = useState<string>('')

  const baseUrl = `${window.location.origin}/v1`
  const activeKey = keys.find((k) => k.is_active)
  const keyDisplay = activeKey ? '你的 API 密钥' : '<your-api-key>'
  const sampleModel = models[0]?.model_id || 'auto:text'

  useEffect(() => {
    apiKeysApi.list().then(setKeys).catch(() => {})
    channelsApi.list().then(setChannels).catch(() => {})
    modelsApi.list({ free_only: true }).then(setModels).catch(() => {})
  }, [])

  // Auto-select first options
  useEffect(() => {
    if (channels.length > 0 && !selectedChannelId) {
      setSelectedChannelId(channels[0].id)
    }
  }, [channels])

  useEffect(() => {
    if (keys.length > 0 && !selectedKeyId) {
      setSelectedKeyId(keys.find(k => k.is_active)?.id || keys[0].id)
    }
  }, [keys])

  useEffect(() => {
    if (selectedChannelId) {
      const channelModels = models.filter(m => m.channel_id === selectedChannelId)
      if (channelModels.length > 0 && (!selectedModel || !channelModels.find(m => m.model_id === selectedModel))) {
        setSelectedModel(channelModels[0].model_id)
      }
    }
  }, [selectedChannelId, models])

  const filteredModels = selectedChannelId
    ? models.filter(m => m.channel_id === selectedChannelId)
    : models

  async function runTest() {
    if (!selectedModel || !testMessage || isStreaming) return

    setIsStreaming(true)
    setStreamFinished(false)
    setTestResponse('')
    setTestError('')

    try {
      const apiKey = keys.find(k => k.id === selectedKeyId)
      if (!apiKey) {
        throw new Error('请选择API密钥')
      }

      const response = await fetch('/v1/chat/completions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey.key}`
        },
        body: JSON.stringify({
          model: selectedModel,
          messages: [{ role: 'user', content: testMessage }],
          stream: true
        })
      })

      if (!response.ok) {
        const error = await response.text()
        throw new Error(`HTTP ${response.status}: ${error}`)
      }

      const reader = response.body?.getReader()
      const decoder = new TextDecoder()

      if (!reader) {
        throw new Error('无法读取响应流')
      }

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        const chunk = decoder.decode(value, { stream: true })
        const lines = chunk.split('\n')

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6)
            if (data === '[DONE]') continue

            try {
              const parsed = JSON.parse(data)
              const content = parsed.choices?.[0]?.delta?.content
              if (content) {
                setTestResponse(prev => prev + content)
              }
            } catch (e) {
              // Ignore parse errors for incomplete chunks
            }
          }
        }
      }
    } catch (err) {
      setTestError(err instanceof Error ? err.message : '未知错误')
    } finally {
      setIsStreaming(false)
      setStreamFinished(true)
    }
  }

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
    <div className="max-w-4xl mx-auto px-4 py-6 space-y-6">
      <div>
        <h1 className="text-lg font-bold text-gray-900">API 文档</h1>
        <p className="text-xs text-gray-400 mt-0.5">使用 OpenAI 兼容接口调用算力池</p>
      </div>

      {/* Online Test Panel */}
      <div className="bg-white border border-gray-200 rounded-2xl p-5 space-y-4 shadow-sm">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-gray-900">🧪 在线测试</h2>
          <div className="flex gap-1">
            {TEST_TABS.map((t) => (
              <button
                key={t.id}
                onClick={() => {
                  setTab(t.id)
                  // Sync codeTab when clicking cURL/Python/Node.js tabs
                  if (t.id !== 'test') {
                    setCodeTab(t.id as CodeTab)
                  }
                }}
                className={`text-xs px-3 py-1.5 rounded-lg transition-colors ${
                  tab === t.id ? 'bg-blue-600 text-white' : 'text-gray-500 hover:bg-gray-100'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>

        {tab === 'test' ? (
          <div className="space-y-4">
            {/* Selectors */}
            <div className="grid grid-cols-3 gap-3">
              {/* Channel Selection */}
              <div>
                <label className="text-xs text-gray-500 block mb-1.5">厂商</label>
                <select
                  value={selectedChannelId}
                  onChange={(e) => setSelectedChannelId(e.target.value)}
                  className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  {channels.map((ch) => (
                    <option key={ch.id} value={ch.id}>
                      {ch.name}
                    </option>
                  ))}
                </select>
              </div>

              {/* Model Selection */}
              <div>
                <label className="text-xs text-gray-500 block mb-1.5">模型</label>
                <select
                  value={selectedModel}
                  onChange={(e) => setSelectedModel(e.target.value)}
                  className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  disabled={!selectedChannelId}
                >
                  {filteredModels.map((m) => (
                    <option key={m.id} value={m.model_id}>
                      {m.display_name || m.model_id}
                    </option>
                  ))}
                </select>
              </div>

              {/* API Key Selection */}
              <div>
                <label className="text-xs text-gray-500 block mb-1.5">API 密钥</label>
                <select
                  value={selectedKeyId}
                  onChange={(e) => setSelectedKeyId(e.target.value)}
                  className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  {keys.map((k) => (
                    <option key={k.id} value={k.id}>
                      {k.name} ({k.key_prefix})
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {/* Test Message Input */}
            <div>
              <label className="text-xs text-gray-500 block mb-1.5">测试消息</label>
              <textarea
                value={testMessage}
                onChange={(e) => setTestMessage(e.target.value)}
                placeholder="输入你想测试的内容..."
                rows={3}
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
                disabled={isStreaming}
              />
            </div>

            {/* Action Buttons */}
            <div className="flex items-center gap-2">
              <button
                onClick={runTest}
                disabled={!selectedModel || !testMessage || isStreaming}
                className={`text-sm px-4 py-2 rounded-lg transition-colors ${
                  !selectedModel || !testMessage || isStreaming
                    ? 'bg-gray-300 text-gray-500 cursor-not-allowed'
                    : 'bg-blue-600 text-white hover:bg-blue-700'
                }`}
              >
                {isStreaming ? '🔄 测试中...' : '▶️ 发送测试'}
              </button>
              {testResponse && (
                <button
                  onClick={() => { setTestResponse(''); setTestError(''); setStreamFinished(true); }}
                  className="text-sm px-4 py-2 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 transition-colors"
                >
                  🗑️ 清空结果
                </button>
              )}
            </div>

            {/* Response Area */}
            {(testResponse || testError || isStreaming) && (
              <div className="border border-gray-200 rounded-xl overflow-hidden">
                <div className="bg-gray-50 px-3 py-2 border-b border-gray-200 flex items-center justify-between">
                  <span className="text-xs font-medium text-gray-700">
                    响应结果
                    {streamFinished && testResponse && <span className="ml-2 text-green-600">✓ 完成</span>}
                  </span>
                  {testResponse && (
                    <button
                      onClick={() => navigator.clipboard.writeText(testResponse)}
                      className="text-xs text-blue-600 hover:text-blue-700"
                    >
                      复制
                    </button>
                  )}
                </div>
                <div className="p-4 min-h-[120px] max-h-[300px] overflow-y-auto">
                  {testError ? (
                    <div className="text-red-600 text-sm whitespace-pre-wrap">{testError}</div>
                  ) : testResponse ? (
                    <div className="text-sm text-gray-800 whitespace-pre-wrap">{testResponse}</div>
                  ) : isStreaming ? (
                    <div className="text-sm text-gray-400">等待响应...</div>
                  ) : null}
                </div>
              </div>
            )}

            {/* Status Hints */}
            {!selectedChannelId && (
              <div className="text-xs text-amber-600 bg-amber-50 rounded-lg px-3 py-2">
                ⚠️ 请先选择厂商
              </div>
            )}
            {selectedChannelId && filteredModels.length === 0 && (
              <div className="text-xs text-amber-600 bg-amber-50 rounded-lg px-3 py-2">
                ⚠️ 该厂商暂无可用模型
              </div>
            )}
          </div>
        ) : (
          <>
            {/* Code examples for non-test tabs */}
            <div className="bg-gray-900 text-gray-100 rounded-xl p-4 text-xs font-mono overflow-x-auto leading-relaxed">
              <pre>{tab === 'curl' ? chatCode.curl : tab === 'python' ? chatCode.python : chatCode.node}</pre>
            </div>
          </>
        )}
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
            {CODE_TABS.map((t) => (
              <button
                key={t.id}
                onClick={() => setCodeTab(t.id)}
                className={`text-xs px-3 py-1.5 rounded-lg transition-colors ${
                  codeTab === t.id ? 'bg-gray-900 text-white' : 'text-gray-500 hover:bg-gray-100'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
          <CodeBlock code={chatCode[codeTab]} id={`chat-${codeTab}`} />
        </div>

        {/* Models */}
        <div className="space-y-3 pt-4 border-t border-gray-100">
          <div>
            <code className="text-xs font-mono bg-green-50 text-green-700 px-2 py-0.5 rounded">GET</code>
            <code className="text-sm font-mono ml-2">/models</code>
          </div>
          <p className="text-sm text-gray-600">列出所有可用的免费模型。</p>
          <CodeBlock code={listCode[codeTab]} id={`list-${codeTab}`} />
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
        <div className="flex gap-1">
          {CODE_TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setCodeTab(t.id)}
              className={`text-xs px-3 py-1.5 rounded-lg transition-colors ${
                codeTab === t.id ? 'bg-gray-900 text-white' : 'text-gray-500 hover:bg-gray-100'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <CodeBlock code={autoCode[codeTab]} id={`auto-${codeTab}`} />
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
