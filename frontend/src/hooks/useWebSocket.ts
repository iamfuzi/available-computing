import { useEffect, useEffectEvent } from 'react'

type Handler = (event: string, data: unknown) => void

export function useWebSocket(onMessage: Handler) {
  const handleMessage = useEffectEvent(onMessage)

  useEffect(() => {
    let socket: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined
    let retryCount = 0
    let disposed = false

    function connect() {
      const token = localStorage.getItem('token')
      if (!token || disposed) return

      const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
      socket = new WebSocket(`${protocol}://${location.host}/ws/events?token=${encodeURIComponent(token)}`)

      socket.onmessage = (e) => {
        try {
          const { event, data } = JSON.parse(e.data)
          handleMessage(event, data)
        } catch {
          // ignore malformed messages
        }
      }

      socket.onclose = () => {
        if (disposed) return
        const delay = Math.min(3000 * Math.pow(2, retryCount), 30000)
        retryCount += 1
        reconnectTimer = setTimeout(connect, delay)
      }

      socket.onerror = () => {
        socket?.close()
      }

      socket.onopen = () => {
        retryCount = 0
      }

      // Keep-alive ping every 30s
      const ping = setInterval(() => {
        if (socket?.readyState === WebSocket.OPEN) socket.send('ping')
      }, 30000)

      socket.addEventListener('close', () => clearInterval(ping))
    }

    connect()
    return () => {
      disposed = true
      clearTimeout(reconnectTimer)
      socket?.close()
    }
  }, [])
}
