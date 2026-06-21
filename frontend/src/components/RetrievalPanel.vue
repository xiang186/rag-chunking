<script setup lang="ts">
/**
 * RetrievalPanel.vue - 高级检索面板
 *
 * 功能：
 * 1. 语义检索：输入查询文本，自动向量化后执行余弦相似度搜索
 * 2. 元数据硬过滤：动态添加/移除 key-value 过滤条件
 * 3. Top-K 控制
 * 4. 检索结果展示（分数、文本预览、元数据）
 * 5. 结果排序与详情查看
 */

import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Search, Plus, ArrowUp, ArrowDown } from '@element-plus/icons-vue'
import { searchChunks } from '@/api/documents'
import type { SearchResultItem, RetrievalFilter } from '@/types/chunking'

const visible = ref(false)

// ── 检索参数 ──
const query = ref('')
const topK = ref(10)
const filters = ref<RetrievalFilter[]>([])

// ── 新过滤条件输入 ──
const newFilterKey = ref('')
const newFilterValue = ref('')

// ── 检索状态 ──
const searching = ref(false)
const results = ref<SearchResultItem[]>([])
const totalResults = ref(0)
const searched = ref(false)

// ── 展开详情 ──
const expandedRow = ref<string | null>(null)

// ── 添加过滤条件 ──
function addFilter() {
  const key = newFilterKey.value.trim()
  const value = newFilterValue.value.trim()
  if (!key || !value) {
    ElMessage.warning('请填写过滤条件的键和值')
    return
  }
  filters.value.push({ key, value })
  newFilterKey.value = ''
  newFilterValue.value = ''
}

function removeFilter(index: number) {
  filters.value.splice(index, 1)
}

// ── 执行检索 ──
async function handleSearch() {
  if (!query.value.trim()) {
    ElMessage.warning('请输入查询文本')
    return
  }

  searching.value = true
  searched.value = true

  // 构建 metadata_filters 字典
  const metadataFilters: Record<string, string> = {}
  for (const f of filters.value) {
    metadataFilters[f.key] = f.value
  }

  try {
    const res = await searchChunks({
      query: query.value,
      top_k: topK.value,
      metadata_filters: metadataFilters,
    })
    results.value = res.results
    totalResults.value = res.total_results

    if (res.results.length === 0) {
      ElMessage.info('未找到匹配结果')
    } else {
      ElMessage.success(`检索完成，共匹配 ${res.total_results} 条，显示前 ${res.results.length} 条`)
    }
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } }; message?: string }
    const msg = err?.response?.data?.detail || err?.message || '检索失败'
    results.value = []
    totalResults.value = 0
    ElMessage.error(msg)
  } finally {
    searching.value = false
  }
}

// ── 分数颜色映射 ──
function scoreColor(score: number): string {
  if (score >= 0.7) return '#67c23a'
  if (score >= 0.5) return '#e6a23c'
  if (score >= 0.3) return '#f56c6c'
  return '#909399'
}

function scoreBg(score: number): string {
  if (score >= 0.7) return '#f0f9eb'
  if (score >= 0.5) return '#fdf6ec'
  if (score >= 0.3) return '#fef0f0'
  return '#f5f7fa'
}

// ── 打开面板 ──
function open() {
  visible.value = true
}

function toggleExpand(chunkId: string) {
  expandedRow.value = expandedRow.value === chunkId ? null : chunkId
}

defineExpose({ open })
</script>

<template>
  <el-drawer
    v-model="visible"
    title="高级语义检索"
    size="640px"
    direction="rtl"
  >
    <div class="retrieval-panel">
      <!-- 检索配置 -->
      <el-card shadow="never" class="search-config">
        <el-form label-position="top">
          <el-form-item label="查询文本">
            <el-input
              v-model="query"
              type="textarea"
              :rows="3"
              placeholder="输入查询内容，支持自然语言描述…"
              clearable
            />
          </el-form-item>

          <el-form-item label="Top-K 返回条数">
            <el-slider
              v-model="topK"
              :min="1"
              :max="50"
              :step="1"
              show-input
              style="padding: 0 16px"
            />
          </el-form-item>

          <el-form-item label="元数据过滤条件（可选）">
            <!-- 已有过滤条件标签 -->
            <div v-if="filters.length > 0" class="filter-tags">
              <el-tag
                v-for="(f, i) in filters"
                :key="i"
                closable
                size="small"
                type="warning"
                style="margin: 2px"
                @close="removeFilter(i)"
              >
                {{ f.key }} = {{ f.value }}
              </el-tag>
            </div>
            <!-- 新增过滤条件 -->
            <div class="filter-input-row">
              <el-input
                v-model="newFilterKey"
                placeholder="字段名"
                size="small"
                style="width: 140px"
                clearable
              />
              <el-input
                v-model="newFilterValue"
                placeholder="值"
                size="small"
                style="width: 160px"
                clearable
                @keyup.enter="addFilter"
              />
              <el-button
                size="small"
                type="primary"
                :icon="Plus"
                @click="addFilter"
                :disabled="!newFilterKey.trim() || !newFilterValue.trim()"
              />
            </div>
          </el-form-item>
        </el-form>

        <el-button
          type="primary"
          :loading="searching"
          :icon="Search"
          style="width: 100%; margin-top: 8px"
          @click="handleSearch"
          size="large"
        >
          {{ searching ? '检索中…' : '执行检索' }}
        </el-button>
      </el-card>

      <!-- 检索结果 -->
      <el-card v-if="searched" shadow="never" class="search-results">
        <template #header>
          <div class="result-header">
            <span>
              检索结果
              <el-tag v-if="results.length > 0" size="small" type="success" style="margin-left: 8px">
                {{ totalResults }} 条匹配
              </el-tag>
              <el-tag v-else size="small" type="info" style="margin-left: 8px">
                无结果
              </el-tag>
            </span>
          </div>
        </template>

        <!-- 结果列表 -->
        <div v-if="results.length > 0" class="result-list">
          <div
            v-for="item in results"
            :key="item.chunk_id"
            class="result-item"
            :class="{ 'result-item--expanded': expandedRow === item.chunk_id }"
          >
            <div class="result-header-row" @click="toggleExpand(item.chunk_id)">
              <div class="result-score" :style="{ backgroundColor: scoreBg(item.score) }">
                <span class="score-value" :style="{ color: scoreColor(item.score) }">
                  {{ (item.score * 100).toFixed(1) }}
                </span>
                <span class="score-label">分</span>
              </div>
              <div class="result-summary">
                <div class="result-text-preview">{{ item.text.slice(0, 120) }}…</div>
                <div class="result-meta-tags">
                  <el-tag size="small" type="info">doc: {{ item.doc_id.slice(0, 8) }}…</el-tag>
                  <el-tag size="small" type="info">chunk: {{ item.chunk_id.slice(0, 8) }}…</el-tag>
                </div>
              </div>
              <el-icon class="expand-icon">
                <component :is="expandedRow === item.chunk_id ? ArrowUp : ArrowDown" />
              </el-icon>
            </div>

            <!-- 展开详情 -->
            <div v-if="expandedRow === item.chunk_id" class="result-detail">
              <div class="detail-section">
                <div class="detail-label">完整文本：</div>
                <div class="detail-text">{{ item.text }}</div>
              </div>
              <div v-if="Object.keys(item.metadata).length > 0" class="detail-section">
                <div class="detail-label">元数据：</div>
                <el-descriptions :column="2" border size="small">
                  <el-descriptions-item
                    v-for="(val, key) in item.metadata"
                    :key="key"
                    :label="key"
                  >
                    {{ typeof val === 'object' ? JSON.stringify(val) : String(val) }}
                  </el-descriptions-item>
                </el-descriptions>
              </div>
              <div class="detail-section">
                <div class="detail-label">ID 信息：</div>
                <el-descriptions :column="2" border size="small">
                  <el-descriptions-item label="Chunk ID">{{ item.chunk_id }}</el-descriptions-item>
                  <el-descriptions-item label="文档 ID">{{ item.doc_id }}</el-descriptions-item>
                  <el-descriptions-item label="相似度分数">{{ item.score.toFixed(4) }}</el-descriptions-item>
                </el-descriptions>
              </div>
            </div>
          </div>
        </div>

        <!-- 无结果 -->
        <el-empty v-else description="未找到匹配结果，请调整查询或过滤条件重试" />
      </el-card>
    </div>
  </el-drawer>
</template>

<style scoped>
.retrieval-panel {
  /* padding: 0 4px; */
}

.search-config {
  margin-bottom: 16px;
}

.filter-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 2px;
  margin-bottom: 8px;
}

.filter-input-row {
  display: flex;
  gap: 6px;
  align-items: center;
}

.search-results {
  margin-bottom: 16px;
}

.result-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
  font-size: 14px;
}

.result-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.result-item {
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  overflow: hidden;
  transition: border-color 0.2s;
}

.result-item:hover {
  border-color: #409eff;
}

.result-item--expanded {
  border-color: #409eff;
  box-shadow: 0 0 0 1px rgba(64, 158, 255, 0.2);
}

.result-header-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  cursor: pointer;
}

.result-score {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-width: 56px;
  height: 48px;
  border-radius: 6px;
  flex-shrink: 0;
}

.score-value {
  font-weight: 700;
  font-size: 16px;
  line-height: 1;
}

.score-label {
  font-size: 10px;
  color: #909399;
  margin-top: 2px;
}

.result-summary {
  flex: 1;
  min-width: 0;
}

.result-text-preview {
  font-size: 13px;
  color: #303133;
  line-height: 1.5;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.result-meta-tags {
  display: flex;
  gap: 4px;
  margin-top: 4px;
}

.expand-icon {
  color: #909399;
  flex-shrink: 0;
}

.result-detail {
  padding: 12px;
  border-top: 1px solid #ebeef5;
  background: #fafafa;
}

.detail-section {
  margin-bottom: 12px;
}

.detail-section:last-child {
  margin-bottom: 0;
}

.detail-label {
  font-weight: 600;
  font-size: 12px;
  color: #606266;
  margin-bottom: 6px;
}

.detail-text {
  font-size: 13px;
  color: #303133;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 200px;
  overflow-y: auto;
  padding: 8px;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
}
</style>
