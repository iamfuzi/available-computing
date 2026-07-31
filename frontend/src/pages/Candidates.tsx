import { useEffect, useMemo, useRef, useState } from 'react'
import { candidatesApi } from '../api/client'
import type { CandidateProvider, CandidateSourceState } from '../api/client'

type DetailView = 'review_required' | 'openai_compatible' | 'excluded' | 'sources'

const detailViewLabels: Record<DetailView, string> = {
  review_required: '可继续审核',
  openai_compatible: 'OpenAI 兼容候选',
  excluded: '准入排除',
  sources: '抓取来源',
}

export default function Candidates() {
  const [candidates, setCandidates] = useState<CandidateProvider[]>([])
  const [sources, setSources] = useState<CandidateSourceState[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [yamlDraft, setYamlDraft] = useState<{ name: string; yaml: string } | null>(null)
  const [detailView, setDetailView] = useState<DetailView | null>(null)
  const detailRef = useRef<HTMLDivElement>(null)

  async function load() {
    setError('')
    try {
      const [candidateRows, sourceRows] = await Promise.all([
        candidatesApi.list(false),
        candidatesApi.sources(),
      ])
      setCandidates(candidateRows)
      setSources(sourceRows)
    } catch {
      setError('候选池加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  async function refresh() {
    setRefreshing(true)
    setError('')
    try {
      const result = await candidatesApi.refresh()
      if (Object.keys(result.failures).length > 0) {
        setError(`部分来源抓取失败：${Object.keys(result.failures).join('、')}。旧数据已保留。`)
      }
      await load()
    } catch {
      setError('候选池刷新失败，旧数据未被覆盖')
    } finally {
      setRefreshing(false)
    }
  }

  async function showDraft(candidate: CandidateProvider) {
    try {
      const result = await candidatesApi.yamlDraft(candidate.provider_id)
      setYamlDraft({ name: candidate.name, yaml: result.yaml })
    } catch {
      setError('YAML 草稿生成失败')
    }
  }

  async function review(candidate: CandidateProvider, status: 'reviewed' | 'ignored') {
    try {
      await candidatesApi.review(candidate.provider_id, status)
      await load()
    } catch {
      setError('审核状态更新失败')
    }
  }

  const attentionSources = sources.filter(source => source.needs_attention)
  const candidateCounts: Record<Exclude<DetailView, 'sources'>, number> = {
    review_required: candidates.filter(candidate =>
      candidate.status === 'pending' && candidate.admission_status === 'review_required'
    ).length,
    openai_compatible: candidates.filter(candidate =>
      candidate.compatibility === 'openai_compatible'
    ).length,
    excluded: candidates.filter(candidate =>
      candidate.admission_status === 'excluded'
    ).length,
  }
  const visibleCandidates = useMemo(() => {
    if (detailView === 'review_required') {
      return candidates.filter(candidate =>
        candidate.status === 'pending' && candidate.admission_status === 'review_required'
      )
    }
    if (detailView === 'openai_compatible') {
      return candidates.filter(candidate => candidate.compatibility === 'openai_compatible')
    }
    if (detailView === 'excluded') {
      return candidates.filter(candidate => candidate.admission_status === 'excluded')
    }
    return candidates
  }, [candidates, detailView])

  function showDetail(view: DetailView) {
    setDetailView(view)
    window.requestAnimationFrame(() => {
      detailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }

  function statCard(view: DetailView, value: number, valueClassName = 'text-gray-900') {
    const selected = detailView === view
    return (
      <button
        type="button"
        onClick={() => showDetail(view)}
        aria-pressed={selected}
        className={`group text-left bg-white border rounded-xl p-3 transition-all focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 ${
          selected
            ? 'border-blue-500 ring-1 ring-blue-500 shadow-sm'
            : 'border-gray-200 hover:border-blue-300 hover:shadow-sm'
        }`}
      >
        <div className={`text-xs ${selected ? 'text-blue-600' : 'text-gray-400 group-hover:text-blue-500'}`}>
          {detailViewLabels[view]}
        </div>
        <div className="flex items-end justify-between gap-2 mt-1">
          <div className={`text-xl font-semibold ${valueClassName}`}>{value}</div>
          <span className={`text-[11px] pb-0.5 ${selected ? 'text-blue-600' : 'text-gray-400 group-hover:text-blue-500'}`}>
            {selected ? '正在查看 ↓' : '查看明细 ↓'}
          </span>
        </div>
      </button>
    )
  }

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 space-y-5">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-lg font-bold text-gray-900">候选厂商</h1>
          <p className="text-xs text-gray-400 mt-0.5">社区清单仅用于发现；必须人工核实免费政策、认证门槛和条款后才能接入</p>
        </div>
        <button
          onClick={refresh}
          disabled={refreshing}
          className="bg-gray-900 text-white text-sm px-4 py-2 rounded-xl hover:bg-gray-800 disabled:opacity-50"
        >
          {refreshing ? '抓取并校验中…' : '刷新候选池'}
        </button>
      </div>

      {error && <div className="text-sm text-red-700 bg-red-50 border border-red-100 rounded-xl px-4 py-3">{error}</div>}
      {attentionSources.length > 0 && (
        <div className="text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3">
          {attentionSources.length} 个候选来源连续解析失败，已保留上一次成功数据，请检查解析器。
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {statCard('review_required', candidateCounts.review_required)}
        {statCard('openai_compatible', candidateCounts.openai_compatible)}
        {statCard('excluded', candidateCounts.excluded, 'text-red-600')}
        {statCard('sources', sources.length)}
      </div>

      <div ref={detailRef} className="scroll-mt-5 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-gray-900">
              {detailView ? detailViewLabels[detailView] : '全部候选'}
            </h2>
            <p className="text-xs text-gray-400 mt-0.5">
              {detailView === 'sources'
                ? `共 ${sources.length} 个来源，包含最近抓取状态和候选数量`
                : `共 ${visibleCandidates.length} 个符合当前条件的候选厂商`}
            </p>
          </div>
          {detailView && (
            <button
              type="button"
              onClick={() => setDetailView(null)}
              className="text-xs text-gray-500 border border-gray-200 bg-white px-3 py-1.5 rounded-lg hover:border-blue-300 hover:text-blue-600"
            >
              查看全部候选
            </button>
          )}
        </div>

        {loading ? (
          <div className="text-center py-16 text-sm text-gray-400">加载中…</div>
        ) : detailView === 'sources' ? (
          sources.length === 0 ? (
            <div className="text-center py-16 text-sm text-gray-400 bg-white border border-dashed rounded-2xl">暂无抓取来源</div>
          ) : (
            <div className="space-y-3">
              {sources.map(source => (
                <div key={source.source_id} className="bg-white border border-gray-200 rounded-xl p-4 space-y-3 shadow-sm">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-semibold text-gray-900">{source.source_id}</span>
                        <span className={`text-[11px] px-2 py-0.5 rounded-full ${
                          source.needs_attention
                            ? 'bg-red-50 text-red-700'
                            : 'bg-green-50 text-green-700'
                        }`}>
                          {source.needs_attention ? '需要关注' : '抓取正常'}
                        </span>
                      </div>
                      <a
                        href={source.url}
                        target="_blank"
                        rel="noreferrer"
                        className="block text-xs text-blue-600 hover:text-blue-700 mt-1 break-all"
                      >
                        {source.url} ↗
                      </a>
                    </div>
                    <div className="text-right shrink-0">
                      <div className="text-lg font-semibold text-gray-900">{source.last_candidate_count}</div>
                      <div className="text-[11px] text-gray-400">最近候选数</div>
                    </div>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-xs">
                    <div className="bg-gray-50 rounded-lg px-3 py-2">
                      <div className="text-gray-400">连续失败</div>
                      <div className={source.consecutive_failures > 0 ? 'text-red-600 mt-0.5' : 'text-gray-700 mt-0.5'}>
                        {source.consecutive_failures} 次
                      </div>
                    </div>
                    <div className="bg-gray-50 rounded-lg px-3 py-2">
                      <div className="text-gray-400">最近尝试</div>
                      <div className="text-gray-700 mt-0.5">{source.last_attempt_at ? new Date(source.last_attempt_at).toLocaleString() : '—'}</div>
                    </div>
                    <div className="bg-gray-50 rounded-lg px-3 py-2">
                      <div className="text-gray-400">最近成功</div>
                      <div className="text-gray-700 mt-0.5">{source.last_success_at ? new Date(source.last_success_at).toLocaleString() : '—'}</div>
                    </div>
                  </div>
                  {source.last_error && (
                    <div className="text-xs text-red-700 bg-red-50 border border-red-100 rounded-lg px-3 py-2 break-words">
                      最近错误：{source.last_error}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )
        ) : candidates.length === 0 ? (
          <div className="text-center py-16 text-sm text-gray-400 bg-white border border-dashed rounded-2xl">暂无待审核候选，点击刷新候选池</div>
        ) : visibleCandidates.length === 0 ? (
          <div className="text-center py-16 text-sm text-gray-400 bg-white border border-dashed rounded-2xl">
            当前没有“{detailView ? detailViewLabels[detailView] : ''}”候选
          </div>
        ) : (
          <div className="space-y-3">
            {visibleCandidates.map(candidate => (
              <div key={candidate.provider_id} className="bg-white border border-gray-200 rounded-xl p-4 space-y-3 shadow-sm">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <a href={candidate.homepage_url} target="_blank" rel="noreferrer" className="font-semibold text-gray-900 hover:text-blue-600">
                      {candidate.name} ↗
                    </a>
                    <code className="text-[11px] text-gray-400 bg-gray-50 px-2 py-0.5 rounded">{candidate.provider_id}</code>
                    <span className={`text-[11px] px-2 py-0.5 rounded-full ${candidate.compatibility === 'openai_compatible' ? 'bg-green-50 text-green-700' : 'bg-amber-50 text-amber-700'}`}>
                      {candidate.compatibility === 'openai_compatible' ? 'OpenAI 兼容候选' : '需自定义评估'}
                    </span>
                    <span className={`text-[11px] px-2 py-0.5 rounded-full ${candidate.admission_status === 'excluded' ? 'bg-red-50 text-red-700' : 'bg-blue-50 text-blue-700'}`}>
                      {candidate.admission_status === 'excluded' ? '不符合准入' : '待官方复核'}
                    </span>
                  </div>
                  <span className="text-xs text-gray-400">发现 {candidate.model_count} 个模型 · {candidate.sources.length} 个来源</span>
                </div>
                {candidate.summary && <p className="text-sm text-gray-600">{candidate.summary}</p>}
                {candidate.exclusion_reason && (
                  <div className="text-xs text-red-700 bg-red-50 border border-red-100 rounded-lg px-3 py-2">
                    排除原因：{candidate.exclusion_reason}
                  </div>
                )}
                {candidate.base_url && <code className="block text-xs bg-gray-50 text-gray-600 rounded-lg px-3 py-2 break-all">{candidate.base_url}</code>}
                <div className="flex flex-wrap gap-2 pt-1 border-t border-gray-50">
                  <button onClick={() => showDraft(candidate)} disabled={candidate.admission_status === 'excluded'} className="text-xs border border-blue-200 text-blue-700 px-3 py-1.5 rounded-lg hover:bg-blue-50 disabled:opacity-40 disabled:cursor-not-allowed">
                    查看 YAML 草稿
                  </button>
                  <button onClick={() => review(candidate, 'reviewed')} disabled={candidate.admission_status === 'excluded'} className="text-xs border border-green-200 text-green-700 px-3 py-1.5 rounded-lg hover:bg-green-50 disabled:opacity-40 disabled:cursor-not-allowed">
                    标记已审核
                  </button>
                  <button onClick={() => review(candidate, 'ignored')} className="text-xs border border-gray-200 text-gray-500 px-3 py-1.5 rounded-lg hover:bg-gray-50 ml-auto">
                    忽略
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {yamlDraft && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-3xl max-h-[90vh] flex flex-col">
            <div className="flex items-center justify-between px-5 py-4 border-b">
              <div>
                <div className="font-semibold">{yamlDraft.name} YAML 草稿</div>
                <div className="text-xs text-amber-600 mt-0.5">草稿不是接入许可；TODO 与官方条款必须人工确认</div>
              </div>
              <button onClick={() => setYamlDraft(null)} className="text-gray-400 hover:text-gray-700">✕</button>
            </div>
            <pre className="m-4 p-4 bg-gray-900 text-gray-100 rounded-xl text-xs overflow-auto whitespace-pre-wrap">{yamlDraft.yaml}</pre>
            <div className="px-5 pb-4 flex justify-end">
              <button onClick={() => navigator.clipboard.writeText(yamlDraft.yaml)} className="text-sm bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700">复制草稿</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
