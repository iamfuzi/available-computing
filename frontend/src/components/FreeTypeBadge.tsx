interface Props {
  freeType: string | null
  source?: string | null
}

const TYPE_CONFIG: Record<string, { label: string; className: string; tooltip: string }> = {
  permanent: {
    label: '永久免费',
    className: 'bg-green-100 text-green-800 border border-green-200',
    tooltip: '该模型永久免费，无额度上限',
  },
  quota: {
    label: '免费配额',
    className: 'bg-yellow-100 text-yellow-800 border border-yellow-200',
    tooltip: '有每日/月额度上限，超出后按量计费',
  },
  grant: {
    label: '新用户赠送',
    className: 'bg-blue-100 text-blue-800 border border-blue-200',
    tooltip: '注册赠送的一次性额度，用完即止',
  },
  unknown: {
    label: '未知',
    className: 'bg-gray-100 text-gray-600 border border-gray-200',
    tooltip: '免费状态未知，请查阅厂商文档确认后再使用',
  },
}

export default function FreeTypeBadge({ freeType, source }: Props) {
  const key = freeType ?? 'unknown'
  const cfg = TYPE_CONFIG[key] ?? TYPE_CONFIG.unknown
  const title = source ? `${cfg.tooltip}\n来源: ${source}` : cfg.tooltip

  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${cfg.className}`}
      title={title}
    >
      {cfg.label}
    </span>
  )
}
