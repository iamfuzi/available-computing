interface Props {
  freeType: string | null
  source?: string | null
  isFree?: boolean | null
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
  billing_suspect: {
    label: '疑似收费',
    className: 'bg-amber-100 text-amber-800 border border-amber-300',
    tooltip: '检测到连续计费信号，等待人工确认免费或收费',
  },
  unknown: {
    label: '未知',
    className: 'bg-gray-100 text-gray-600 border border-gray-200',
    tooltip: '免费状态未知，请查阅厂商文档确认后再使用',
  },
}

const PAID_CONFIG = {
  label: '已确认收费',
  className: 'bg-red-50 text-red-700 border border-red-200',
  tooltip: '人工确认或厂商信号判定为收费，已移出免费池',
}

const PENDING_CONFIG = {
  label: '待人工确认',
  className: 'bg-orange-100 text-orange-800 border border-orange-300',
  tooltip: '免费状态未确认，请在模型详情页人工裁定',
}

export default function FreeTypeBadge({ freeType, source, isFree }: Props) {
  // is_free is the authoritative billing flag; free_type alone can mislead
  // (a paid prefix-rule verdict keeps free_type="permanent").
  let cfg = TYPE_CONFIG[freeType ?? 'unknown'] ?? TYPE_CONFIG.unknown
  if (isFree === false) {
    cfg = PAID_CONFIG
  } else if (isFree == null) {
    cfg = PENDING_CONFIG
  }
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
