interface Props {
  status: string
  responseMs?: number | null
}

const STATUS_CONFIG: Record<string, { dot: string; label: string }> = {
  healthy: { dot: 'bg-green-500', label: 'text-green-700' },
  slow:    { dot: 'bg-yellow-400', label: 'text-yellow-700' },
  down:    { dot: 'bg-red-500', label: 'text-red-700' },
  unknown: { dot: 'bg-gray-400', label: 'text-gray-500' },
}

export default function HealthBadge({ status, responseMs }: Props) {
  const cfg = STATUS_CONFIG[status] ?? STATUS_CONFIG.unknown
  const label = responseMs != null ? `${responseMs}ms` : status
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`w-2 h-2 rounded-full ${cfg.dot}`} />
      <span className={`text-sm ${cfg.label}`}>{label}</span>
    </span>
  )
}
