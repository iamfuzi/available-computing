interface Props {
  label: string
  value: string | number
  sub?: string
  onClick?: () => void
}

export default function StatCard({ label, value, sub, onClick }: Props) {
  return (
    <div
      className={`bg-white border border-gray-200 rounded-xl p-4 flex flex-col gap-1 ${onClick ? 'cursor-pointer hover:border-blue-300 transition-colors' : ''}`}
      onClick={onClick}
    >
      <span className="text-sm text-gray-500">{label}</span>
      <span className="text-2xl font-semibold text-gray-900">{value}</span>
      {sub && <span className="text-xs text-gray-400">{sub}</span>}
    </div>
  )
}
