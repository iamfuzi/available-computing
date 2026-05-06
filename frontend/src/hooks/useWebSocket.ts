import { useEffect, useRef, useCallback } from 'react'

type Handler = (event: string, data: unknown) => void

export function useWebSocket(onMessage: Handler) {
  const ws = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const handlerRef = useRef(onMessage)
  handlerRef.current = onMessage
  const retryCount = useRef(0)

  const connect = useCallback(() => {
    const token = localStorage.getItem('token')
    if (!token) return

    const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
    const socket = new WebSocket(`${protocol}://${location.host}/ws/events?token=${encodeURIComponent(token)}`)

    socket.onmessage = (e) => {
      try {
        const { event, data } = JSON.parse(e.data)
        handlerRef.current(event, data)
      } catch {
        // ignore malformed messages
      }
    }

    socket.onclose = () => {
      const delay = Math.min(3000 * Math.pow(2, retryCount.current), 30000)
      retryCount.current += 1
      reconnectTimer.current = setTimeout(connect, delay)
    }

    socket.onerror = () => {
      socket.close()
    }

    socket.onopen = () => {
      retryCount.current = 0
    }

    // Keep-alive ping every 30s
    const ping = setInterval(() => {
      if (socket.readyState === WebSocket.OPEN) socket.send('ping')
    }, 30000)

    socket.addEventListener('close', () => clearInterval(ping))
    ws.current = socket
  }, [])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnectTimer.current)
      ws.current?.close()
    }
  }, [connect])
}
