import { BrowserRouter, Routes, Route, NavLink, Navigate } from 'react-router-dom'
import Pool from './pages/Pool'
import Channels from './pages/Channels'
import ModelDetail from './pages/ModelDetail'
import Settings from './pages/Settings'
import Login from './pages/Login'

function RequireAuth({ children }: { children: React.ReactNode }) {
  const token = localStorage.getItem('token')
  if (!token) return <Navigate to="/login" replace />
  return <>{children}</>
}

function Layout({ children }: { children: React.ReactNode }) {
  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-2 text-sm px-3 py-2 rounded-lg transition-colors ${
      isActive
        ? 'bg-blue-600 text-white shadow-sm'
        : 'text-gray-600 hover:bg-gray-100'
    }`

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col md:flex-row">
      {/* Desktop sidebar */}
      <aside className="hidden md:flex w-52 bg-white border-r border-gray-200 flex-col py-5 px-3 gap-1 shrink-0 sticky top-0 h-screen">
        <div className="px-3 mb-6">
          <div className="text-base font-bold text-gray-900">⚡ Available</div>
          <div className="text-xs text-gray-400 -mt-0.5">Computing</div>
        </div>
        <nav className="flex flex-col gap-1">
          <NavLink to="/" end className={linkClass}>
            <span>📊</span><span>算力池</span>
          </NavLink>
          <NavLink to="/channels" className={linkClass}>
            <span>🔌</span><span>厂商管理</span>
          </NavLink>
          <NavLink to="/settings" className={linkClass}>
            <span>⚙️</span><span>设置</span>
          </NavLink>
        </nav>
        <div className="mt-auto px-1">
          <button
            onClick={() => { localStorage.removeItem('token'); window.location.href = '/login' }}
            className="text-xs text-gray-400 hover:text-red-500 transition-colors px-2 py-1"
          >
            退出登录
          </button>
        </div>
      </aside>

      {/* Mobile bottom nav */}
      <nav className="md:hidden fixed bottom-0 left-0 right-0 bg-white border-t border-gray-200 flex z-40">
        {[
          { to: '/', end: true, icon: '📊', label: '算力池' },
          { to: '/channels', icon: '🔌', label: '厂商' },
          { to: '/settings', icon: '⚙️', label: '设置' },
        ].map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) =>
              `flex-1 flex flex-col items-center py-2 text-xs transition-colors ${
                isActive ? 'text-blue-600' : 'text-gray-400'
              }`
            }
          >
            <span className="text-lg">{item.icon}</span>
            <span>{item.label}</span>
          </NavLink>
        ))}
        <button
          onClick={() => { localStorage.removeItem('token'); window.location.href = '/login' }}
          className="flex-1 flex flex-col items-center py-2 text-xs text-gray-400 transition-colors"
        >
          <span className="text-lg">🚪</span>
          <span>退出</span>
        </button>
      </nav>

      {/* Main content with bottom padding on mobile for nav */}
      <main className="flex-1 overflow-auto pb-20 md:pb-0">{children}</main>
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<RequireAuth><Layout><Pool /></Layout></RequireAuth>} />
        <Route path="/channels" element={<RequireAuth><Layout><Channels /></Layout></RequireAuth>} />
        <Route path="/models/:id" element={<RequireAuth><Layout><ModelDetail /></Layout></RequireAuth>} />
        <Route path="/settings" element={<RequireAuth><Layout><Settings /></Layout></RequireAuth>} />
      </Routes>
    </BrowserRouter>
  )
}
