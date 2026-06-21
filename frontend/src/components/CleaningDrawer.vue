<script setup lang="ts">
/**
 * CleaningDrawer.vue - 数据清洗管道配置面板
 *
 * 功能：
 * 1. 5 种清洗策略的开关与参数配置
 * 2. 输入待清洗文本并执行清洗
 * 3. 展示清洗结果（变更记录 + 前后对比）
 */

import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Brush, Delete, CircleCheck, Warning } from '@element-plus/icons-vue'
import { cleanText } from '@/api/documents'
import type { CleaningConfig, CleaningChange, CleaningResult } from '@/types/chunking'

const visible = ref(false)

// ── 清洗配置（与后端 CleaningConfigRequest 一一对应） ──
const config = reactive<CleaningConfig>({
  enable_heuristic: true,
  enable_layout: false,
  enable_pii: false,
  enable_semantic_filter: false,
  layout_backend: 'unstructured',
  use_presidio: false,
  custom_filter_rules: [],
})

// ── 自定义过滤规则输入 ──
const newRule = ref('')

// ── 待清洗文本 ──
const inputText = ref('')
const previewBefore = ref('')
const previewAfter = ref('')

// ── 清洗状态 ──
const cleaning = ref(false)
const result = ref<CleaningResult | null>(null)

// ── 添加自定义规则 ──
function addRule() {
  const trimmed = newRule.value.trim()
  if (!trimmed) return
  config.custom_filter_rules.push(trimmed)
  newRule.value = ''
}

function removeRule(index: number) {
  config.custom_filter_rules.splice(index, 1)
}

// ── 执行清洗测试 ──
async function handleClean() {
  if (!inputText.value.trim()) {
    ElMessage.warning('请输入待清洗的文本')
    return
  }

  cleaning.value = true
  previewBefore.value = inputText.value
  result.value = null

  try {
    const res = await cleanText({
      text: inputText.value,
      enable_heuristic: config.enable_heuristic,
      enable_layout: config.enable_layout,
      enable_pii: config.enable_pii,
      enable_semantic_filter: config.enable_semantic_filter,
      layout_backend: config.layout_backend,
      use_presidio: config.use_presidio,
      custom_filter_rules: config.custom_filter_rules,
    })
    result.value = res
    previewAfter.value = res.text
    if (res.cleaned) {
      ElMessage.success(`清洗完成，共 ${res.changes.length} 项变更`)
    } else {
      ElMessage.info('清洗完成，未检测到需要修改的内容')
    }
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } }; message?: string }
    ElMessage.error(err?.response?.data?.detail || err?.message || '清洗执行失败')
  } finally {
    cleaning.value = false
  }
}

// ── 清空结果 ──
function reset() {
  inputText.value = ''
  result.value = null
  previewBefore.value = ''
  previewAfter.value = ''
}

// ── 清洗变更类型对应的样式 ──
function changeTagType(change: CleaningChange) {
  if (change.op === 'replace' || change.op === 'redact') return 'danger'
  if (change.op === 'remove') return 'warning'
  if (change.op === 'normalize') return 'primary'
  return 'info'
}

// ── 对外暴露 ──
function open(initialConfig?: CleaningConfig) {
  if (initialConfig) {
    Object.assign(config, initialConfig)
  }
  visible.value = true
}

function getConfig(): CleaningConfig {
  return { ...config }
}

/** 触发父组件的 apply 回调 */
const emit = defineEmits<{ apply: [config: CleaningConfig] }>()

function handleApply() {
  emit('apply', { ...config })
  visible.value = false
}

defineExpose({ open, getConfig })
</script>

<template>
  <el-drawer
    v-model="visible"
    title="数据清洗管道配置"
    size="600px"
    direction="rtl"
  >
    <div class="cleaning-drawer">
      <!-- 清洗策略开关 -->
      <el-card shadow="never" class="config-section">
        <template #header>
          <div class="section-title">
            <el-icon><Brush /></el-icon>
            清洗策略
          </div>
        </template>

        <el-form label-position="top">
          <el-form-item label="启发式清洗">
            <el-switch v-model="config.enable_heuristic" />
            <div class="strategy-desc">
              乱码修复（ftfy）+ 空白字符标准化（换行符归一化、首尾空格修剪）
            </div>
          </el-form-item>

          <el-form-item label="版面感知清洗">
            <el-switch v-model="config.enable_layout" />
            <div class="strategy-desc">PDF 多栏布局合并、页眉页脚/页码检测移除</div>
            <el-select
              v-if="config.enable_layout"
              v-model="config.layout_backend"
              style="width: 100%; margin-top: 8px"
              size="small"
            >
              <el-option label="unstructured" value="unstructured" />
              <el-option label="basic" value="basic" />
            </el-select>
          </el-form-item>

          <el-form-item label="PII 隐私脱敏">
            <el-switch v-model="config.enable_pii" />
            <div class="strategy-desc">手机号、邮箱、身份证号自动脱敏</div>
            <el-checkbox
              v-if="config.enable_pii"
              v-model="config.use_presidio"
              style="margin-top: 8px"
              size="small"
            >
              增强模式（presidio NLP 引擎，需本地模型）
            </el-checkbox>
          </el-form-item>

          <el-form-item label="语义过滤">
            <el-switch v-model="config.enable_semantic_filter" />
            <div class="strategy-desc">去除免责声明、页码标记、版权信息等无关元素</div>
          </el-form-item>

          <!-- 自定义过滤规则 -->
          <el-form-item v-if="config.enable_semantic_filter" label="自定义过滤规则（正则）">
            <div class="rule-list">
              <el-tag
                v-for="(rule, i) in config.custom_filter_rules"
                :key="i"
                closable
                size="small"
                style="margin: 2px"
                @close="removeRule(i)"
              >
                {{ rule }}
              </el-tag>
            </div>
            <div class="rule-input-row">
              <el-input
                v-model="newRule"
                placeholder="输入正则模式，如 ^第 [0-9]+ 页$"
                size="small"
                clearable
                @keyup.enter="addRule"
              />
              <el-button size="small" type="primary" @click="addRule" :disabled="!newRule.trim()">
                添加
              </el-button>
            </div>
          </el-form-item>
        </el-form>
      </el-card>

      <!-- 测试清洗 -->
      <el-card shadow="never" class="config-section">
        <template #header>
          <div class="section-title">
            <el-icon><CircleCheck /></el-icon>
            测试清洗管道
          </div>
        </template>

        <el-input
          v-model="inputText"
          type="textarea"
          :rows="6"
          placeholder="输入待清洗的文本，点击下方按钮执行测试…"
        />

        <div class="actions-row">
          <el-button type="primary" :loading="cleaning" @click="handleClean">
            {{ cleaning ? '清洗中…' : '执行清洗测试' }}
          </el-button>
          <el-button :disabled="!result && !inputText" @click="reset">
            <el-icon><Delete /></el-icon>
            清空
          </el-button>
        </div>
      </el-card>

      <!-- 清洗结果 -->
      <el-card v-if="result" shadow="never" class="config-section">
        <template #header>
          <div class="section-title">
            <el-icon :class="result.cleaned ? 'icon-success' : 'icon-info'">
              <component :is="result.cleaned ? CircleCheck : Warning" />
            </el-icon>
            清洗结果
            <el-tag v-if="result.cleaned" type="danger" size="small" style="margin-left: 8px">
              已修改
            </el-tag>
            <el-tag v-else type="info" size="small" style="margin-left: 8px">
              未变更
            </el-tag>
          </div>
        </template>

        <!-- 前后对比 -->
        <div class="diff-section">
          <div class="sub-title">前后对比</div>
          <el-row :gutter="12">
            <el-col :span="12">
              <div class="diff-panel">
                <div class="diff-panel-header">原始文本</div>
                <div class="diff-text">{{ previewBefore }}</div>
              </div>
            </el-col>
            <el-col :span="12">
              <div class="diff-panel">
                <div class="diff-panel-header">
                  清洗后文本
                  <el-tag v-if="result.cleaned" type="success" size="small" effect="dark" style="margin-left: 6px">
                    已清洗
                  </el-tag>
                </div>
                <div class="diff-text diff-text--cleaned">{{ result.text }}</div>
              </div>
            </el-col>
          </el-row>
        </div>

        <!-- 变更记录 -->
        <div v-if="result.changes.length > 0" class="changes-section">
          <div class="sub-title">变更记录（共 {{ result.changes.length }} 项）</div>
          <el-table :data="result.changes" size="small" max-height="280" style="width: 100%">
            <el-table-column prop="stage" label="阶段" width="120" />
            <el-table-column prop="op" label="操作" width="88">
              <template #default="{ row }">
                <el-tag :type="changeTagType(row)" size="small">{{ row.op }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="详情" min-width="260">
              <template #default="{ row }">
                <div class="change-detail">
                  <!-- detail 描述文本（所有变更都有） -->
                  <span class="detail-text">{{ row.detail || '-' }}</span>
                  <!-- pii 脱敏类型 -->
                  <span v-if="row.pii_type" class="detail-tag">
                    <el-tag size="small" type="warning">PII: {{ row.pii_type }}</el-tag>
                  </span>
                  <!-- 语义过滤匹配规则 -->
                  <span v-if="row.rule" class="detail-tag">
                    <el-tag size="small" type="info">规则: {{ row.rule }}</el-tag>
                  </span>
                  <!-- 原始值与清洗后值（当 backend 提供了时） -->
                  <div v-if="row.original || row.cleaned" class="detail-values">
                    <span v-if="row.original" class="value-original">
                      原始: <code>{{ row.original }}</code>
                    </span>
                    <span v-if="row.cleaned" class="value-cleaned">
                      清洗后: <code>{{ row.cleaned }}</code>
                    </span>
                  </div>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="count" label="数量" width="60" align="center" />
          </el-table>
        </div>

        <!-- 清洗元数据 -->
        <div v-if="result.metadata && Object.keys(result.metadata).length > 0" class="meta-section">
          <div class="sub-title">元数据</div>
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item
              v-for="(val, key) in result.metadata"
              :key="key"
              :label="key"
            >
              {{ typeof val === 'object' ? JSON.stringify(val) : val }}
            </el-descriptions-item>
          </el-descriptions>
        </div>
      </el-card>
    </div>

    <!-- 底部操作栏 -->
    <div class="drawer-footer">
      <el-button type="primary" size="large" style="width: 100%" @click="handleApply">
        应用配置到分块流程
      </el-button>
    </div>
  </el-drawer>
</template>

<style scoped>
.cleaning-drawer {
  /* padding: 0 8px; */
}

.config-section {
  margin-bottom: 16px;
}

.section-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  font-size: 14px;
}

.icon-success {
  color: #67c23a;
}

.icon-info {
  color: #909399;
}

.strategy-desc {
  font-size: 12px;
  color: #909399;
  line-height: 1.5;
  margin-top: 4px;
}

.rule-list {
  display: flex;
  flex-wrap: wrap;
  gap: 2px;
  margin-bottom: 8px;
}

.rule-input-row {
  display: flex;
  gap: 8px;
}

.actions-row {
  margin-top: 12px;
  display: flex;
  gap: 12px;
}

.changes-section {
  margin-bottom: 16px;
}

.change-detail {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.detail-text {
  font-size: 12px;
  color: #606266;
  line-height: 1.5;
}

.detail-tag {
  display: inline-block;
}

.detail-values {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 2px;
}

.value-original,
.value-cleaned {
  font-size: 11px;
  color: #606266;
}

.value-original code {
  background: #fef0f0;
  color: #f56c6c;
  padding: 0 4px;
  border-radius: 2px;
  font-size: 11px;
}

.value-cleaned code {
  background: #f0f9eb;
  color: #67c23a;
  padding: 0 4px;
  border-radius: 2px;
  font-size: 11px;
}

.diff-section {
  margin-bottom: 16px;
}

.diff-panel {
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  overflow: hidden;
}

.diff-panel-header {
  display: flex;
  align-items: center;
  padding: 6px 10px;
  font-size: 12px;
  font-weight: 600;
  color: #606266;
  background: #f5f7fa;
  border-bottom: 1px solid #e4e7ed;
}

.diff-text {
  padding: 10px;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 200px;
  overflow-y: auto;
  color: #303133;
}

.diff-text--cleaned {
  background: #f0f9eb;
}

.sub-title {
  font-weight: 600;
  font-size: 13px;
  color: #303133;
  margin-bottom: 8px;
}

.meta-section {
  margin-top: 12px;
}

.drawer-footer {
  padding: 16px 0;
  border-top: 1px solid #e4e7ed;
  margin-top: 8px;
}
</style>
