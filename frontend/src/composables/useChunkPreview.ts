/**
 * useChunkPreview - 分块预览 Composable Hook
 *
 * 功能：
 * 1. 管理分块预览状态（loading、error、preview data）
 * 2. 参数修改时自动触发防抖（Debounce）预览请求
 * 3. 避免用户快速调整 Slider 时产生大量 API 调用
 */

import { ref, watch, type Ref } from 'vue'
import { previewChunks } from '@/api/documents'
import type { ChunkPreviewResponse, StrategyName } from '@/types/chunking'

const DEBOUNCE_MS = 500

export function useChunkPreview(
  docId: Ref<string | null>,
  strategyName: Ref<StrategyName>,
  params: Ref<Record<string, unknown>>,
  previewLimit: Ref<number>,
  cleaningConfig?: Ref<Record<string, unknown> | null>,
) {
  const previewData = ref<ChunkPreviewResponse | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  let debounceTimer: ReturnType<typeof setTimeout> | null = null

  /** 是否为语义分块（需要调用外部 API，较慢） */
  const isSemantic = () => strategyName.value === 'semantic'

  async function fetchPreview() {
    if (!docId.value) return

    loading.value = true
    error.value = null

    try {
      previewData.value = await previewChunks(
        docId.value,
        strategyName.value,
        params.value,
        previewLimit.value,
        cleaningConfig?.value ?? undefined,
      )
    } catch (e: unknown) {
      const err = e as { code?: string; message?: string }
      const msg = err?.message || '预览请求失败'

      // 超时错误给出更友好的提示
      if (msg.includes('timeout')) {
        if (isSemantic()) {
          error.value =
            '语义分块超时：Embedding API 响应过慢。' +
            '\n请检查网络是否可访问 OpenAI API，或尝试其他策略。'
        } else {
          error.value =
            '分块超时：文档较大或处理耗时较长（超过 3 分钟）。' +
            '\n可尝试缩小分块尺寸或减少预览块数。'
        }
      } else {
        error.value = msg
      }
      previewData.value = null
    } finally {
      loading.value = false
    }
  }

  /** 防抖触发预览 - 参数变化时调用 */
  function debouncedPreview() {
    if (debounceTimer) clearTimeout(debounceTimer)
    debounceTimer = setTimeout(() => {
      fetchPreview()
    }, DEBOUNCE_MS)
  }

  // 监听策略、参数、预览数量变化，自动防抖预览
  watch([docId, strategyName, params, previewLimit], () => {
    if (docId.value) {
      debouncedPreview()
    }
  }, { deep: true })

  return {
    previewData,
    loading,
    error,
    fetchPreview,
    debouncedPreview,
    isSemantic,
  }
}
