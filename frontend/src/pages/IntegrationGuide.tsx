import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { marked } from 'marked'
import { publicApi } from '../api/client'

// 公开的接入手册页（无需登录）。第三方拿到地址 + ac_ key 后从这里
// 自助接入：自检、auto 路由、频率限制、错误契约。内容来自后端打包的
// docs/06-integration.md 原文。
export default function IntegrationGuide() {
  const [html, setHtml] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    publicApi.integrationGuide()
      .then((d) => setHtml(marked.parse(d.markdown, { async: false }) as string))
      .catch(() => setError('手册加载失败，请稍后重试'))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200">
        <div className="max-w-3xl mx-auto px-4 py-4 flex items-center justify-between">
          <div>
            <h1 className="text-base font-bold text-gray-900">AC 服务接入手册</h1>
            <p className="text-xs text-gray-400 mt-0.5">算力池（Available Computing）· 第三方接入指南 · 无需登录</p>
          </div>
          <Link to="/login" className="text-xs text-blue-600 hover:underline">管理登录 →</Link>
        </div>
      </header>
      <main className="max-w-3xl mx-auto px-4 py-6">
        {loading && <p className="text-sm text-gray-400 animate-pulse py-12 text-center">加载中...</p>}
        {error && <p className="text-sm text-red-500 py-12 text-center">{error}</p>}
        {html && (
          <article
            className="prose-ac bg-white border border-gray-200 rounded-2xl p-6 shadow-sm text-sm leading-relaxed text-gray-700 [&_h1]:text-lg [&_h1]:font-bold [&_h1]:text-gray-900 [&_h1]:mt-6 [&_h1]:mb-3 [&_h2]:text-base [&_h2]:font-semibold [&_h2]:text-gray-900 [&_h2]:mt-6 [&_h2]:mb-2 [&_h3]:text-sm [&_h3]:font-semibold [&_h3]:text-gray-900 [&_h3]:mt-4 [&_h3]:mb-2 [&_p]:my-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ul]:my-2 [&_ol]:list-decimal [&_ol]:pl-5 [&_ol]:my-2 [&_li]:my-1 [&_a]:text-blue-600 [&_a]:underline [&_code]:bg-gray-100 [&_code]:px-1 [&_code]:py-0.5 [&_code]:rounded [&_code]:text-xs [&_code]:font-mono [&_pre]:bg-gray-950 [&_pre]:text-gray-200 [&_pre]:rounded-xl [&_pre]:p-4 [&_pre]:text-xs [&_pre]:overflow-x-auto [&_pre]:my-3 [&_pre_code]:bg-transparent [&_pre_code]:text-inherit [&_pre_code]:p-0 [&_table]:w-full [&_table]:text-xs [&_table]:my-3 [&_th]:border [&_th]:border-gray-200 [&_th]:bg-gray-50 [&_th]:px-2 [&_th]:py-1.5 [&_th]:text-left [&_td]:border [&_td]:border-gray-200 [&_td]:px-2 [&_td]:py-1.5 [&_blockquote]:border-l-4 [&_blockquote]:border-blue-200 [&_blockquote]:pl-3 [&_blockquote]:text-gray-500 [&_blockquote]:my-2 [&_hr]:my-4 [&_hr]:border-gray-100"
            dangerouslySetInnerHTML={{ __html: html }}
          />
        )}
      </main>
    </div>
  )
}
