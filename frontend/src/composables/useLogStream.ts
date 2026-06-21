/**
 * useLogStream - 实时日志 WebSocket 连接 Composable
 *
 * 功能：
 * 1. 自动连接后端 WebSocket 日志端点
 * 2. 实时接收日志消息并追加到响应式列表
 * 3. 支持自动重连（断线后指数退避重连）
 * 4. 提供日志清除、过滤等操作
 */

import { computed, onUnmounted, ref } from 'vue'
import type { LogEntry, LogLevel } from '@/types/chunking'

const WS_URL = `ws://${window.location.host}/api/logs/ws`
const MAX_LOGS = 1000
const RECONNECT_BASE_MS = 1000
const MAX_RECONNECT_MS = 30000

export function useLogStream() {
  const logs = ref<LogEntry[]>([])
  const connected = ref(false)
  const connecting = ref(false)
  const levelFilter = ref<LogLevel | 'all'>('all')
  const autoScroll = ref(true)

  let ws: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let reconnectAttempts = 0
  let intentionalClose = false

  const filteredLogs = computed(() => {
    if (levelFilter.value === 'all') return logs.value
    return logs.value.filter((log) => log.level === levelFilter.value)
  })

  const logCountByLevel = computed(() => {
    const counts: Record<string, number> = { debug: 0, info: 0, warn: 0, error: 0, success: 0 }
    for (const log of logs.value) {
      counts[log.level] = (counts[log.level] || 0) + 1
    }
    return counts
  })

  function connect() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      return
    }

    intentionalClose = false
    connecting.value = true

    try {
      ws = new WebSocket(WS_URL)
    } catch {
      scheduleReconnect()
      return
    }

    ws.onopen = () => {
      connected.value = true
      connecting.value = false
      reconnectAttempts = 0
    }

    ws.onmessage = (event) => {
      try {
        const entry: LogEntry = JSON.parse(event.data)
        // 忽略控制消息
        if ('type' in entry && !('level' in entry)) return

        logs.value.push(entry)
        // 限制最大日志数
        if (logs.value.length > MAX_LOGS) {
          logs.value = logs.value.slice(-MAX_LOGS)
        }
      } catch {
        // 忽略非 JSON 消息
      }
    }

    ws.onclose = () => {
      connected.value = false
      connecting.value = false
      if (!intentionalClose) {
        scheduleReconnect()
      }
    }

    ws.onerror = () => {
      connecting.value = false
    }
  }

  function scheduleReconnect() {
    if (intentionalClose) return
    const delay = Math.min(RECONNECT_BASE_MS * Math.pow(2, reconnectAttempts), MAX_RECONNECT_MS)
    reconnectAttempts++
    reconnectTimer = setTimeout(() => {
      connect()
    }, delay)
  }

  function disconnect() {
    intentionalClose = true
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (ws) {
      ws.close()
      ws = null
    }
    connected.value = false
    connecting.value = false
  }

  function clearLogs() {
    logs.value = []
    // 通知服务端清除历史
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: 'clear' }))
    }
  }

  function sendPing() {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: 'ping' }))
    }
  }

  // 自动连接
  connect()

  // 组件卸载时断开
  onUnmounted(() => {
    disconnect()
  })

  return {
    logs: filteredLogs,
    allLogs: logs,
    connected,
    connecting,
    levelFilter,
    autoScroll,
    logCountByLevel,
    connect,
    disconnect,
    clearLogs,
    sendPing,
  }
}
