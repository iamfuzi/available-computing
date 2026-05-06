import { useEffect, useRef, useCallback } from 'react'

type Handler = (event: string, data: unknown) => void

export function useWebSocket(onMessage: Handler) {
  const ws = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const handlerRef = useRef(onMessage)
  handlerRef.current = onMessage

  const connect = useCallback(() => {
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
    const socket = new WebSocket(`${protocol}://${location.host}/ws/events`)

    socket.onmessage = (e) => {
      try {
        const { event, data } = JSON.parse(e.data)
        handlerRef.current(event, data)
      } catch {
        // ignore malformed messages
      }
    }

    socket.onclose = () => {
      reconnectTimer.current = setTimeout(connect, 3000)
    }

    socket.onerror = () => {
      socket.close()
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
