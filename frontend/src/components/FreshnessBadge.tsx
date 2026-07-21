interface Props {
  lastVerifiedAt?: string | null
  thresholdDays?: number | null
  method?: string | null
}

const METHOD_LABEL: Record<string, string> = {
  passive: '真实流量',
  active_baseline: '基线探测',
  active_heartbeat: '心跳探测',
  active_event_triggered: '事件复检',
  active_legacy: '历史主动探测',
  manual: '手动探测',
}

// Freshness is informational and is refreshed whenever the application is
// loaded or reloaded by its existing polling/WebSocket flow.
const PAGE_LOADED_AT_MS = Date.now()

export default function FreshnessBadge({ lastVerifiedAt, thresholdDays = 7, method }: Props) {
  if (!lastVerifiedAt) {
    return (
      <span
        className="inline-flex items-center gap-1 rounded-full border border-gray-300 px-2 py-0.5 text-xs text-gray-500"
        title="尚无成功真实请求记录"
      >
        <span className="h-1.5 w-1.5 rounded-full bg-gray-300" />
        未验证
      </span>
    )
  }

  const verifiedAt = new Date(lastVerifiedAt)
  const ageMs = PAGE_LOADED_AT_MS - verifiedAt.getTime()
  const staleAfterMs = Math.max(1, thresholdDays || 7) * 24 * 60 * 60 * 1000
  const stale = Number.isNaN(ageMs) || ageMs > staleAfterMs
  const methodLabel = method ? (METHOD_LABEL[method] || method) : '未知方式'
  const title = Number.isNaN(verifiedAt.getTime())
    ? '验证时间格式无效'
    : `最近验证：${verifiedAt.toLocaleString()} · ${methodLabel}`

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs ${
        stale
          ? 'border-amber-300 text-amber-700'
          : 'border-green-200 bg-green-50 text-green-700'
      }`}
      title={title}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${stale ? 'bg-transparent ring-1 ring-amber-500' : 'bg-green-500'}`} />
      {stale ? '待确认' : '已验证'}
    </span>
  )
}
