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
    `text-sm px-3 py-2 rounded-lg transition-colors ${isActive ? 'bg-blue-50 text-blue-700 font-medium' : 'text-gray-600 hover:bg-gray-100'}`

  return (
    <div className="min-h-screen bg-gray-50 flex">
      {/* Sidebar */}
      <aside className="w-48 bg-white border-r border-gray-200 flex flex-col py-4 px-3 gap-1 shrink-0">
        <div className="text-sm font-semibold text-gray-900 px-3 py-2 mb-2">
          ⚡ Available Computing
        </div>
        <NavLink to="/" end className={linkClass}>📊 算力池</NavLink>
        <NavLink to="/channels" className={linkClass}>🔌 厂商管理</NavLink>
        <NavLink to="/settings" className={linkClass}>⚙️ 设置</NavLink>
        <div className="mt-auto">
          <button
            onClick={() => { localStorage.removeItem('token'); window.location.href = '/login' }}
            className="text-xs text-gray-400 hover:text-gray-600 px-3 py-2"
          >
            退出登录
          </button>
        </div>
      </aside>
      <main className="flex-1 overflow-auto">{children}</main>
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
