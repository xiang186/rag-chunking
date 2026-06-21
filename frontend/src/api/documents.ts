import axios from 'axios'
import type {
  ChunkExecuteResponse,
  ChunkPreviewResponse,
  StrategyMeta,
  StrategyName,
  UploadResponse,
} from '@/types/chunking'

const api = axios.create({
  baseURL: '/api',
  timeout: 60000,
})

/**
 * 判断是否为需要调用外部 API 或处理大量数据的慢速策略
 */
function isSlowStrategy(strategyName: StrategyName): boolean {
  return strategyName === 'semantic' || strategyName === 'pdf_table_layout'
}

export async function fetchStrategies(): Promise<StrategyMeta[]> {
  const { data } = await api.get<StrategyMeta[]>('/documents/strategies')
  return data
}

export async function uploadDocument(file: File): Promise<UploadResponse> {
  const formData = new FormData()
  formData.append('file', file)
  const { data } = await api.post<UploadResponse>('/documents/upload', formData)
  return data
}

export async function previewChunks(
  docId: string,
  strategyName: StrategyName,
  params: Record<string, unknown>,
  previewLimit: number = 500,
  cleaningConfig?: Record<string, unknown>,
): Promise<ChunkPreviewResponse> {
  // 语义分块需调用 OpenAI Embedding API，超时设为 3 分钟
  const timeout = isSlowStrategy(strategyName) ? 180000 : 60000
  const body: Record<string, unknown> = {
    strategy_name: strategyName,
    params,
    preview_limit: previewLimit,
  }
  if (cleaningConfig) {
    body.cleaning_config = cleaningConfig
  }
  const { data } = await api.post<ChunkPreviewResponse>(
    `/documents/${docId}/preview`,
    body,
    { timeout },
  )
  return data
}

export async function executeChunking(
  docId: string,
  strategyName: StrategyName,
  params: Record<string, unknown>,
  cleaningConfig?: Record<string, unknown>,
): Promise<ChunkExecuteResponse> {
  // 语义分块执行也需要更长的超时
  const timeout = isSlowStrategy(strategyName) ? 180000 : 60000
  const body: Record<string, unknown> = {
    strategy_name: strategyName,
    params,
  }
  if (cleaningConfig) {
    body.cleaning_config = cleaningConfig
  }
  const { data } = await api.post<ChunkExecuteResponse>(
    `/documents/${docId}/execute`,
    body,
    { timeout },
  )
  return data
}

export interface TestEmbeddingResult {
  success: boolean
  duration_ms: number
  vector_dim?: number
  vector_preview?: string
  message: string
  error?: string
}

// ── 数据清洗 API ──

export interface CleanTextRequest {
  text: string
  enable_heuristic: boolean
  enable_layout: boolean
  enable_pii: boolean
  enable_semantic_filter: boolean
  layout_backend: string
  use_presidio: boolean
  custom_filter_rules: string[]
}

export async function cleanText(
  req: CleanTextRequest,
): Promise<import('@/types/chunking').CleaningResult> {
  const { data } = await api.post<import('@/types/chunking').CleaningResult>(
    '/v1/retrieval/clean-text',
    req,
    { timeout: 120000 },
  )
  return data
}

// ── 向量检索 API ──

export interface SearchRequest {
  query: string
  top_k: number
  metadata_filters: Record<string, string>
}

export async function searchChunks(
  req: SearchRequest,
): Promise<import('@/types/chunking').SearchResponse> {
  const { data } = await api.post<import('@/types/chunking').SearchResponse>(
    '/v1/retrieval/search',
    req,
    { timeout: 120000 },
  )
  return data
}

export async function testEmbedding(
  apiKey: string,
  baseUrl: string,
  model: string,
): Promise<TestEmbeddingResult> {
  const { data } = await api.post<TestEmbeddingResult>(
    '/documents/test-embedding',
    { api_key: apiKey, base_url: baseUrl, model },
    { timeout: 60000 },
  )
  return data
}
