<script setup lang="ts">
/**
 * ChunkingConfig.vue - 分块配置与预览主组件
 *
 * 功能模块：
 * 1. 文件上传区域
 * 2. 分块策略 Radio 选择器（附带适用场景说明）
 * 3. 动态参数配置表单（Slider / Switch / Select）
 * 4. 预览区域：左右分栏，左侧原文，右侧高亮分块边界
 */

import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { UploadFilled, Document, Setting, View, Loading, Brush, Search as SearchIcon } from '@element-plus/icons-vue'
import { fetchStrategies, uploadDocument, executeChunking, testEmbedding } from '@/api/documents'
import { useChunkPreview } from '@/composables/useChunkPreview'
import LogPanel from '@/components/LogPanel.vue'
import CleaningDrawer from '@/components/CleaningDrawer.vue'
import RetrievalPanel from '@/components/RetrievalPanel.vue'
import type { StrategyMeta, StrategyName } from '@/types/chunking'
import { CHUNK_COLORS } from '@/types/chunking'

// ── 状态 ──
const strategies = ref<StrategyMeta[]>([])
const docId = ref<string | null>(null)
const uploadedFilename = ref<string>('')
const selectedStrategy = ref<StrategyName>('recursive_character')
const strategyParams = ref<Record<string, unknown>>({})
const executing = ref(false)
const previewLimit = ref(500)
const sourceTextRef = ref<HTMLElement | null>(null)
const activeChunkIndex = ref<number | null>(null)

// 每个策略的用户自定义参数缓存（切换策略时不丢失已填写的值）
const savedParamsPerStrategy = ref<Record<string, Record<string, unknown>>>({})

// ── 预览块数上限：跟随当前文档总块数 ──
const maxPreviewLimit = computed(() => previewData.value?.total_chunks ?? 500)

// 修正非法预览块数：≤0 / NaN / 超出上限时自动纠正
function clampPreviewLimit() {
  const v = previewLimit.value
  const max = maxPreviewLimit.value
  if (!v || isNaN(v) || v <= 0) {
    previewLimit.value = 1
  } else if (v > max) {
    previewLimit.value = max
  }
}

// ── 当前策略元信息 ──
const currentStrategyMeta = computed(() =>
  strategies.value.find((s) => s.name === selectedStrategy.value),
)

// ── 清洗配置（与 CleaningDrawer 共享） ──
const cleaningConfig = ref<Record<string, unknown> | null>(null)
const cleaningEnabled = ref(false)
// 清洗配置变化时重新预览
watch(cleaningConfig, () => {
  if (docId.value) debouncedPreview()
}, { deep: true })
watch(cleaningEnabled, () => {
  if (docId.value) debouncedPreview()
})

// ── 预览 Hook（含防抖） ──
const { previewData, loading: previewLoading, error: previewError, fetchPreview, debouncedPreview, isSemantic } =
  useChunkPreview(docId, selectedStrategy, strategyParams, previewLimit, cleaningConfig)

// ── 初始化：加载策略列表 ──
onMounted(async () => {
  try {
    strategies.value = await fetchStrategies()
    if (strategies.value.length > 0) {
      selectedStrategy.value = strategies.value[0].name
      strategyParams.value = { ...strategies.value[0].default_params }
    }
  } catch {
    ElMessage.error('加载分块策略失败')
  }
})

// 切换策略时保存当前参数，恢复目标策略的上次填写值
watch(selectedStrategy, (name, oldName) => {
  // 保存当前策略参数
  if (oldName) {
    savedParamsPerStrategy.value[oldName] = { ...strategyParams.value }
  }

  const meta = strategies.value.find((s) => s.name === name)
  if (!meta) return

  // 优先从缓存恢复，其次用默认值
  const cached = savedParamsPerStrategy.value[name]
  if (cached) {
    strategyParams.value = { ...cached }
  } else {
    strategyParams.value = { ...meta.default_params }
  }
})

// 参数变化时自动保存到缓存（确保滑动滑块、输入文本等实时保存）
watch(strategyParams, (val) => {
  if (selectedStrategy.value) {
    savedParamsPerStrategy.value[selectedStrategy.value] = { ...val }
  }
}, { deep: true })

// 预览数据变化时，确保 previewLimit 不超过 total_chunks
watch(maxPreviewLimit, (newMax) => {
  if (previewLimit.value > newMax) {
    previewLimit.value = newMax
  }
})

// ── 文件上传 ──
async function handleUpload(file: File) {
  try {
    const res = await uploadDocument(file)
    docId.value = res.doc_id
    uploadedFilename.value = res.filename
    ElMessage.success(`上传成功: ${res.filename}`)
    await fetchPreview()
  } catch {
    ElMessage.error('文件上传失败')
  }
  return false // 阻止 el-upload 默认上传行为
}

// ── 执行全量分块 ──
async function handleExecute() {
  if (!docId.value) {
    ElMessage.warning('请先上传文档')
    return
  }
  executing.value = true
  try {
    const cc = cleaningEnabled.value && cleaningConfig.value ? cleaningConfig.value : undefined
    const res = await executeChunking(docId.value, selectedStrategy.value, strategyParams.value, cc)
    ElMessage.success(`${res.message} (Job ID: ${res.job_id})`)
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } }; message?: string }
    const detail = err?.response?.data?.detail
    const msg = detail || err?.message || '分块执行失败'
    ElMessage.error(msg)
  } finally {
    executing.value = false
  }
}

// ── 高亮渲染：将原文按 chunk 边界着色 ──
const highlightedSource = computed(() => {
  if (!previewData.value) return []

  const text = previewData.value.source_text_preview
  // 按 char_start 排序，确保处理顺序与文档位置一致
  const chunks = [...previewData.value.preview_chunks].sort(
    (a, b) => a.char_start - b.char_start || a.char_end - b.char_end,
  )

  const segments: Array<{ text: string; color: string; chunkIndex: number | null }> = []
  let cursor = 0

  for (const chunk of chunks) {
    // 跳过完全超出预览文本范围的块
    if (chunk.char_start >= text.length) {
      continue
    }

    // 处理 overlap：实际渲染起点不能早于 cursor（避免同一段文字重复渲染）
    const renderStart = Math.max(chunk.char_start, cursor)

    // 未覆盖区域（无高亮）
    if (renderStart > cursor) {
      segments.push({ text: text.slice(cursor, renderStart), color: '', chunkIndex: null })
    }

    // 跳过零长度块
    if (chunk.char_end <= renderStart) {
      cursor = renderStart
      continue
    }

    // 高亮块
    const renderEnd = Math.min(chunk.char_end, text.length)
    segments.push({
      text: text.slice(renderStart, renderEnd),
      color: CHUNK_COLORS[chunk.index % CHUNK_COLORS.length],
      chunkIndex: chunk.index,
    })
    cursor = renderEnd
  }

  // 剩余文本
  if (cursor < text.length) {
    segments.push({ text: text.slice(cursor), color: '', chunkIndex: null })
  }

  return segments
})

// ── 点击分块 → 原文自动滚动到对应位置 ──
function scrollToChunk(chunkIndex: number) {
  activeChunkIndex.value = chunkIndex
  if (!sourceTextRef.value) return

  const target = sourceTextRef.value.querySelector<HTMLElement>(
    `[data-chunk-idx="${chunkIndex}"]`,
  )
  if (target) {
    target.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }
}

// ── 测试 Embedding API 连接 ──
const testingEmbedding = ref(false)
const embeddingTestResult = ref<string | null>(null)
const embeddingTestSuccess = ref(false)

async function handleTestEmbedding() {
  const apiKey = (strategyParams.value.openai_api_key as string) || ''
  const baseUrl = (strategyParams.value.openai_base_url as string) || ''
  const model = (strategyParams.value.embedding_model as string) || 'text-embedding-3-small'

  if (!apiKey) {
    ElMessage.warning('请先填写 API Key')
    return
  }

  testingEmbedding.value = true
  embeddingTestResult.value = null
  try {
    const res = await testEmbedding(apiKey, baseUrl, model)
    embeddingTestSuccess.value = res.success
    embeddingTestResult.value = res.success
      ? `✅ ${res.message}`
      : `❌ ${res.message}`
    if (res.success) {
      ElMessage.success(res.message)
    } else {
      ElMessage.error(res.message)
    }
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: { message?: string } } }; message?: string }
    const detail = err?.response?.data?.detail
    const msg = detail?.message || err?.message || '连接测试失败'
    embeddingTestSuccess.value = false
    embeddingTestResult.value = `❌ ${msg}`
    ElMessage.error(msg)
  } finally {
    testingEmbedding.value = false
  }
}

// ── 清洗配置 Drawer ──
const cleaningDrawerRef = ref<InstanceType<typeof CleaningDrawer> | null>(null)

function openCleaningDrawer() {
  // 传递当前已保存的清洗配置，让 drawer 保持同步
  cleaningDrawerRef.value?.open(cleaningConfig.value ?? undefined)
}

function onCleaningApplied(cfg: Record<string, unknown>) {
  cleaningConfig.value = { ...cfg, enable_heuristic: cfg.enable_heuristic ?? true } as Record<string, unknown>
  cleaningEnabled.value = true
  ElMessage.success('清洗配置已应用到分块流程，预览将重新执行')
}

// ── 高级检索面板 ──
const retrievalPanelRef = ref<InstanceType<typeof RetrievalPanel> | null>(null)

function openRetrievalPanel() {
  retrievalPanelRef.value?.open()
}
</script>

<template>
  <div class="chunking-config">
  <el-row :gutter="24">
    <!-- 左侧：配置面板 -->
    <el-col :span="10">
      <!-- 文件上传 -->
      <el-card shadow="hover" class="section-card">
        <template #header>
          <el-icon><UploadFilled /></el-icon>
          文档上传
        </template>
        <el-upload
          drag
          :auto-upload="true"
          :show-file-list="false"
          accept=".txt,.md,.pdf,.markdown"
          :before-upload="handleUpload"
        >
          <el-icon class="upload-icon"><UploadFilled /></el-icon>
          <div class="el-upload__text">
            拖拽文件到此处，或 <em>点击上传</em>
          </div>
          <template #tip>
            <div class="el-upload__tip">支持 .txt / .md / .pdf 格式</div>
          </template>
        </el-upload>
        <div v-if="uploadedFilename" class="uploaded-info">
          <el-icon><Document /></el-icon>
          {{ uploadedFilename }}
          <el-tag size="small" type="success">已上传</el-tag>
        </div>
      </el-card>

      <!-- 策略选择 -->
      <el-card shadow="hover" class="section-card">
        <template #header>
          <el-icon><Setting /></el-icon>
          分块策略
        </template>
        <el-radio-group v-model="selectedStrategy" class="strategy-group">
          <el-radio
            v-for="strategy in strategies"
            :key="strategy.name"
            :value="strategy.name"
            class="strategy-radio"
          >
            <div class="strategy-label">{{ strategy.label }}</div>
            <div class="strategy-desc">{{ strategy.description }}</div>
          </el-radio>
        </el-radio-group>
      </el-card>

      <!-- 动态参数表单 -->
      <el-card v-if="currentStrategyMeta" shadow="hover" class="section-card">
        <template #header>参数配置</template>
        <el-form label-position="top">
          <el-form-item
            v-for="param in currentStrategyMeta.param_schema"
            :key="param.key"
            :label="param.label"
          >
            <!-- Slider -->
            <el-slider
              v-if="param.type === 'slider'"
              v-model="strategyParams[param.key]"
              :min="param.min"
              :max="param.max"
              :step="param.step"
              show-input
            />
            <!-- Switch -->
            <el-switch
              v-else-if="param.type === 'switch'"
              v-model="strategyParams[param.key]"
            />
            <!-- Select -->
            <el-select
              v-else-if="param.type === 'select'"
              v-model="strategyParams[param.key]"
              style="width: 100%"
            >
              <el-option
                v-for="opt in param.options"
                :key="opt"
                :label="opt"
                :value="opt"
              />
            </el-select>
            <!-- Text input (API Key, Base URL, Model name) -->
            <el-input
              v-else-if="param.type === 'text'"
              v-model="strategyParams[param.key]"
              :placeholder="param.placeholder || ''"
              :type="param.key === 'openai_api_key' ? 'password' : 'text'"
              :show-password="param.key === 'openai_api_key'"
              clearable
            />
            <!-- 参数说明 -->
            <div v-if="param.description" class="param-desc">{{ param.description }}</div>
          </el-form-item>
        </el-form>

        <!-- 测试 Embedding 连接（语义分块 / 对话体分块） -->
        <template v-if="selectedStrategy === 'semantic' || selectedStrategy === 'dialogue_aware'">
          <div class="embedding-test-area">
            <el-button
              type="default"
              :loading="testingEmbedding"
              @click="handleTestEmbedding"
              size="small"
            >
              {{ testingEmbedding ? '测试中...' : '测试连接' }}
            </el-button>
            <span
              v-if="embeddingTestResult"
              class="embedding-test-result"
              :class="{ 'test-pass': embeddingTestSuccess, 'test-fail': !embeddingTestSuccess }"
            >
              {{ embeddingTestResult }}
            </span>
          </div>
        </template>

        <el-button
          type="primary"
          :loading="executing"
          :disabled="!docId"
          @click="handleExecute"
          style="width: 100%; margin-top: 16px"
        >
          执行全量分块并入库
        </el-button>

        <div v-if="cleaningEnabled" class="cleaning-status">
          <el-tag type="success" size="small" effect="dark" closable @close="cleaningEnabled = false; cleaningConfig = null">
            清洗已启用
          </el-tag>
        </div>

        <el-button
          type="info"
          :icon="Brush"
          @click="openCleaningDrawer"
          style="width: 100%; margin-top: 8px"
          plain
        >
          数据清洗管道
        </el-button>
      </el-card>
    </el-col>

    <!-- 右侧：预览区域 -->
    <el-col :span="14">
      <el-card shadow="hover" class="section-card preview-card">
        <template #header>
          <div class="preview-header">
            <span class="preview-title">
              <el-icon><View /></el-icon>
              分块预览
              <el-tag v-if="previewData" size="small" style="margin-left: 8px">
                共 {{ previewData.total_chunks }} 块，预览前 {{ previewData.preview_chunks.length }} 块
              </el-tag>
            </span>
            <span class="preview-limit-control">
              预览块数
              <el-input-number
                v-model="previewLimit"
                :min="1"
                :max="maxPreviewLimit"
                :step="1"
                size="small"
                style="width: 130px"
                @change="clampPreviewLimit"
                @blur="clampPreviewLimit"
              />
            </span>
          </div>
        </template>

        <div v-if="!docId" class="empty-state">
          <el-empty description="请先上传文档以预览分块效果" />
        </div>

        <div v-else-if="previewLoading" class="loading-state">
          <el-skeleton :rows="8" animated />
          <div v-if="isSemantic()" class="semantic-loading-tip">
            <el-icon class="is-loading"><Loading /></el-icon>
            语义分块正在调用 Embedding API 计算文本向量，首次请求可能需要 10-30 秒…
          </div>
        </div>

        <div v-else-if="previewError" class="error-state">
          <el-alert :title="previewError" type="error" show-icon />
        </div>

        <div v-else-if="previewData" class="preview-panel">
          <!-- 左右分栏预览 -->
          <el-row :gutter="16">
            <!-- 左侧：原文 -->
            <el-col :span="12">
              <div class="preview-column">
                <div class="column-title">原始文本</div>
                <div ref="sourceTextRef" class="text-panel source-text">
                  <span
                    v-for="(seg, i) in highlightedSource"
                    :key="i"
                    :data-chunk-idx="seg.chunkIndex"
                    :style="seg.color ? { backgroundColor: seg.color } : {}"
                    :class="{
                      'chunk-highlight': seg.chunkIndex !== null,
                      'chunk-highlight--active': seg.chunkIndex === activeChunkIndex,
                    }"
                    :title="seg.chunkIndex !== null ? `Chunk #${seg.chunkIndex}` : ''"
                  >{{ seg.text }}</span>
                </div>
              </div>
            </el-col>

            <!-- 右侧：分块列表 -->
            <el-col :span="12">
              <div class="preview-column">
                <div class="column-title">分块结果</div>
                <div class="chunks-list">
                  <div
                    v-for="chunk in previewData.preview_chunks"
                    :key="chunk.index"
                    class="chunk-item"
                    :class="{ 'chunk-item--active': chunk.index === activeChunkIndex }"
                    :style="{ backgroundColor: CHUNK_COLORS[chunk.index % CHUNK_COLORS.length] }"
                    @click="scrollToChunk(chunk.index)"
                  >
                    <div class="chunk-header">
                      <el-tag size="small" type="info">#{{ chunk.index }}</el-tag>
                      <span class="chunk-range">
                        [{{ chunk.char_start }} - {{ chunk.char_end }}]
                      </span>
                      <span class="chunk-len">{{ chunk.text.length }} 字符</span>
                    </div>
                    <div class="chunk-text">{{ chunk.text }}</div>
                    <div v-if="Object.keys(chunk.metadata).length" class="chunk-meta">
                      <el-tag
                        v-for="(val, key) in chunk.metadata"
                        :key="key"
                        size="small"
                        type="warning"
                        style="margin: 2px"
                      >
                        {{ key }}: {{ val }}
                      </el-tag>
                    </div>
                  </div>
                </div>
              </div>
            </el-col>
          </el-row>
        </div>
      </el-card>

      <!-- 实时日志面板 -->
      <LogPanel />
      <el-button
        type="success"
        :icon="SearchIcon"
        @click="openRetrievalPanel"
        style="width: 100%; margin-top: 8px"
        plain
      >
        高级语义检索
      </el-button>
    </el-col>
  </el-row>

  <!-- 清洗配置 Drawer -->
  <CleaningDrawer ref="cleaningDrawerRef" @apply="onCleaningApplied" />

  <!-- 高级检索 Drawer -->
  <RetrievalPanel ref="retrievalPanelRef" />
  </div>
</template>

<style scoped>
.chunking-config {
  padding: 24px;
  max-width: 1400px;
  margin: 0 auto;
}

.section-card {
  margin-bottom: 16px;
}

.upload-icon {
  font-size: 48px;
  color: #409eff;
}

.uploaded-info {
  margin-top: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  color: #606266;
}

.strategy-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.strategy-radio {
  height: auto;
  margin-right: 0;
  padding: 8px 12px;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  transition: border-color 0.2s;
}

.strategy-radio:hover {
  border-color: #409eff;
}

.strategy-label {
  font-weight: 600;
  font-size: 14px;
}

.strategy-desc {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}

.preview-card {
  min-height: 600px;
}

.preview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}

.preview-title {
  display: flex;
  align-items: center;
  gap: 0;
}

.preview-limit-control {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #606266;
  white-space: nowrap;
}

.preview-panel {
  height: 100%;
}

.preview-column {
  height: 100%;
}

.column-title {
  font-weight: 600;
  font-size: 14px;
  color: #303133;
  margin-bottom: 8px;
  padding-bottom: 8px;
  border-bottom: 1px solid #e4e7ed;
}

.text-panel {
  height: 520px;
  overflow-y: auto;
  padding: 12px;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  font-size: 13px;
  line-height: 1.8;
  white-space: pre-wrap;
  word-break: break-all;
}

.chunk-highlight {
  border-radius: 2px;
  padding: 1px 0;
  cursor: default;
  transition: outline 0.25s, outline-offset 0.25s;
}

.chunk-highlight--active {
  outline: 2px solid #409eff;
  outline-offset: 1px;
  border-radius: 3px;
  animation: chunk-flash 0.6s ease;
}

@keyframes chunk-flash {
  0% { outline-color: transparent; }
  30% { outline-color: #409eff; }
  100% { outline-color: #409eff; }
}

.chunks-list {
  height: 520px;
  overflow-y: auto;
}

.chunk-item {
  padding: 10px 12px;
  margin-bottom: 8px;
  border-radius: 6px;
  border: 1px solid #e4e7ed;
  cursor: pointer;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.chunk-item:hover {
  border-color: #409eff;
}

.chunk-item--active {
  border-color: #409eff;
  box-shadow: 0 0 0 2px rgba(64, 158, 255, 0.3);
}

.chunk-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.chunk-range {
  font-size: 11px;
  color: #909399;
}

.chunk-len {
  font-size: 11px;
  color: #909399;
}

.chunk-text {
  font-size: 13px;
  line-height: 1.6;
  color: #303133;
  max-height: 120px;
  overflow-y: auto;
}

.chunk-meta {
  margin-top: 6px;
}

.empty-state,
.loading-state,
.error-state {
  padding: 40px 0;
}

.cleaning-status {
  margin-top: 8px;
  text-align: center;
}

.semantic-loading-tip {
  margin-top: 12px;
  text-align: center;
  color: #e6a23c;
  font-size: 13px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.embedding-test-area {
  padding: 0 0 16px 0;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.embedding-test-result {
  font-size: 12px;
  line-height: 1.4;
}

.embedding-test-result.test-pass {
  color: #67c23a;
}

.embedding-test-result.test-fail {
  color: #f56c6c;
}

.param-desc {
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
  margin-top: 4px;
  padding: 4px 8px;
  background: #fafafa;
  border-radius: 4px;
  border-left: 3px solid #e4e7ed;
}
</style>
