<script setup lang="ts">
/**
 * LogPanel.vue - 实时日志面板组件
 *
 * 功能：
 * 1. 通过 WebSocket 实时接收后端日志
 * 2. 按级别过滤（debug/info/warn/error/success）
 * 3. 自动滚动到最新日志
 * 4. 支持清除日志、暂停/恢复自动滚动
 * 5. 日志条目带时间戳、级别标签、来源标识、耗时显示
 */

import { nextTick, ref, watch } from 'vue'
import { Delete, VideoPause, VideoPlay } from '@element-plus/icons-vue'
import { useLogStream } from '@/composables/useLogStream'
import type { LogLevel } from '@/types/chunking'

const {
  logs,
  connected,
  connecting,
  levelFilter,
  autoScroll,
  logCountByLevel,
  clearLogs,
} = useLogStream()

const logContainer = ref<HTMLElement | null>(null)
const collapsed = ref(false)

// 级别配置：颜色和标签
const levelConfig: Record<string, { color: string; bg: string; label: string }> = {
  debug: { color: '#909399', bg: '#f4f4f5', label: 'DEBUG' },
  info: { color: '#409eff', bg: '#ecf5ff', label: 'INFO' },
  warn: { color: '#e6a23c', bg: '#fdf6ec', label: 'WARN' },
  error: { color: '#f56c6c', bg: '#fef0f0', label: 'ERROR' },
  success: { color: '#67c23a', bg: '#f0f9eb', label: 'OK' },
}

// 过滤选项
const filterOptions: Array<{ value: LogLevel | 'all'; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'debug', label: 'Debug' },
  { value: 'info', label: 'Info' },
  { value: 'warn', label: 'Warn' },
  { value: 'error', label: 'Error' },
  { value: 'success', label: 'Success' },
]

// 格式化时间戳
function formatTime(ts: number): string {
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

// 格式化耗时
function formatDuration(ms: number | undefined): string {
  if (ms === undefined) return ''
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

// 自动滚动
watch(
  () => logs.value.length,
  async () => {
    if (autoScroll.value && logContainer.value) {
      await nextTick()
      logContainer.value.scrollTop = logContainer.value.scrollHeight
    }
  },
)
</script>

<template>
  <el-card shadow="hover" class="log-panel-card">
    <template #header>
      <div class="log-header">
        <div class="log-header-left">
          <span class="log-title">实时日志</span>
          <el-tag
            :type="connected ? 'success' : connecting ? 'warning' : 'danger'"
            size="small"
            effect="dark"
            class="connection-tag"
          >
            {{ connected ? '已连接' : connecting ? '连接中' : '未连接' }}
          </el-tag>
        </div>
        <div class="log-header-right">
          <el-select
            v-model="levelFilter"
            size="small"
            style="width: 100px"
          >
            <el-option
              v-for="opt in filterOptions"
              :key="opt.value"
              :label="`${opt.label}${opt.value !== 'all' ? ` (${logCountByLevel[opt.value] || 0})` : ''}`"
              :value="opt.value"
            />
          </el-select>
          <el-tooltip :content="autoScroll ? '暂停自动滚动' : '恢复自动滚动'">
            <el-button
              size="small"
              :icon="autoScroll ? VideoPause : VideoPlay"
              :type="autoScroll ? 'primary' : 'default'"
              @click="autoScroll = !autoScroll"
            />
          </el-tooltip>
          <el-tooltip content="清除日志">
            <el-button
              size="small"
              :icon="Delete"
              @click="clearLogs"
            />
          </el-tooltip>
          <el-button
            size="small"
            :icon="collapsed ? 'ArrowDown' : 'ArrowUp'"
            @click="collapsed = !collapsed"
          />
        </div>
      </div>
    </template>

    <div
      v-show="!collapsed"
      ref="logContainer"
      class="log-container"
    >
      <div v-if="logs.length === 0" class="log-empty">
        <span class="log-empty-text">{{ connected ? '等待日志...' : '未连接到日志服务' }}</span>
      </div>
      <div
        v-for="log in logs"
        :key="log.id"
        class="log-entry"
        :class="`log-level-${log.level}`"
      >
        <span class="log-time">{{ formatTime(log.timestamp) }}</span>
        <el-tag
          size="small"
          :color="levelConfig[log.level]?.bg"
          :style="{ color: levelConfig[log.level]?.color, borderColor: levelConfig[log.level]?.bg }"
          class="log-level-tag"
        >
          {{ levelConfig[log.level]?.label || log.level }}
        </el-tag>
        <span v-if="log.source" class="log-source">[{{ log.source }}]</span>
        <span class="log-message">{{ log.message }}</span>
        <span v-if="log.duration_ms !== undefined" class="log-duration">
          {{ formatDuration(log.duration_ms) }}
        </span>
      </div>
    </div>
  </el-card>
</template>

<style scoped>
.log-panel-card {
  margin-bottom: 16px;
}

.log-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.log-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.log-header-right {
  display: flex;
  align-items: center;
  gap: 6px;
}

.log-title {
  font-weight: 600;
  font-size: 14px;
}

.connection-tag {
  font-size: 11px;
}

.log-container {
  height: 260px;
  overflow-y: auto;
  background: #1e1e2e;
  border-radius: 6px;
  padding: 8px 0;
  font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
  font-size: 12px;
  line-height: 1.6;
}

.log-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
}

.log-empty-text {
  color: #6c7086;
  font-size: 13px;
}

.log-entry {
  padding: 2px 12px;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  transition: background 0.1s;
}

.log-entry:hover {
  background: rgba(255, 255, 255, 0.04);
}

.log-time {
  color: #6c7086;
  flex-shrink: 0;
  font-size: 11px;
}

.log-level-tag {
  flex-shrink: 0;
  font-size: 10px;
  padding: 0 4px;
  height: 18px;
  line-height: 18px;
  border-radius: 3px;
}

.log-source {
  color: #89b4fa;
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 600;
}

.log-message {
  color: #cdd6f4;
  flex: 1;
  min-width: 0;
  word-break: break-word;
}

.log-duration {
  color: #fab387;
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 600;
}

/* 级别特定样式 */
.log-level-debug .log-message {
  color: #7f849c;
}

.log-level-info .log-message {
  color: #89dceb;
}

.log-level-warn {
  background: rgba(249, 226, 175, 0.06);
}

.log-level-warn .log-message {
  color: #f9e2af;
}

.log-level-error {
  background: rgba(243, 139, 168, 0.08);
}

.log-level-error .log-message {
  color: #f38ba8;
}

.log-level-success .log-message {
  color: #a6e3a1;
}
</style>
