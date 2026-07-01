interface Props {
  status: string
  responseMs?: number | null
}

const STATUS_CONFIG: Record<string, { dot: string; label: string }> = {
  healthy: { dot: 'bg-green-500', label: 'text-green-700' },
  slow:    { dot: 'bg-yellow-400', label: 'text-yellow-700' },
  rate_limited: { dot: 'bg-orange-500', label: 'text-orange-700' },
  down:    { dot: 'bg-red-500', label: 'text-red-700' },
  unknown: { dot: 'bg-gray-400', label: 'text-gray-500' },
}

const STATUS_LABEL: Record<string, string> = {
  healthy: '可用',
  slow: '不稳定',
  rate_limited: '限流中',
  down: '异常',
  unknown: '未验证',
}

export default function HealthBadge({ status, responseMs }: Props) {
  const cfg = STATUS_CONFIG[status] ?? STATUS_CONFIG.unknown
  const label = responseMs != null && status === 'healthy' ? `${responseMs}ms` : (STATUS_LABEL[status] ?? status)
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`w-2 h-2 rounded-full ${cfg.dot}`} />
      <span className={`text-sm ${cfg.label}`}>{label}</span>
    </span>
  )
}
