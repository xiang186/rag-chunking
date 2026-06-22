/** 分块策略相关 TypeScript 类型定义 */

export type StrategyName =
  | 'recursive_character'
  | 'semantic'
  | 'markdown_structure'
  | 'pdf_table_layout'
  | 'parent_child'
  | 'dialogue_aware'
  | 'html_table'
  | 'complex_table'

export interface ParamSchemaItem {
  key: string
  label: string
  type: 'slider' | 'switch' | 'select' | 'text'
  min?: number
  max?: number
  step?: number
  options?: string[]
  placeholder?: string
  description?: string
}

export interface StrategyMeta {
  name: StrategyName
  label: string
  description: string
  default_params: Record<string, unknown>
  param_schema: ParamSchemaItem[]
}

export interface ChunkPreviewItem {
  index: number
  text: string
  metadata: Record<string, unknown>
  char_start: number
  char_end: number
}

export interface ChunkPreviewResponse {
  doc_id: string
  strategy_name: StrategyName
  total_chunks: number
  preview_chunks: ChunkPreviewItem[]
  source_text_preview: string
}

export interface UploadResponse {
  doc_id: string
  filename: string
  file_size: number
  message: string
}

export interface ChunkExecuteResponse {
  job_id: string
  doc_id: string
  strategy_name: StrategyName
  total_chunks: number
  message: string
}

/** 预览高亮用的颜色列表 */
export const CHUNK_COLORS = [
  '#e8f4fd',
  '#fde8e8',
  '#e8fde8',
  '#fdf8e8',
  '#f0e8fd',
  '#e8fdf5',
  '#fde8f4',
  '#e8f0fd',
]

// ── 数据清洗相关类型 ──

export interface CleaningConfig {
  enable_heuristic: boolean
  enable_layout: boolean
  enable_pii: boolean
  enable_semantic_filter: boolean
  layout_backend: string
  use_presidio: boolean
  custom_filter_rules: string[]
}

export interface CleaningChange {
  op: string
  field?: string
  original?: string
  cleaned?: string
  pattern?: string
  count?: number
  reason?: string
  stage?: string
}

export interface CleaningResult {
  text: string
  cleaned: boolean
  changes: CleaningChange[]
  metadata: Record<string, unknown>
}

export interface SearchResultItem {
  chunk_id: string
  doc_id: string
  text: string
  metadata: Record<string, unknown>
  score: number
}

export interface SearchResponse {
  query: string
  total_results: number
  results: SearchResultItem[]
}

export interface RetrievalFilter {
  key: string
  value: string
}

/** 实时日志相关类型 */

export type LogLevel = 'debug' | 'info' | 'warn' | 'error' | 'success'

export interface LogEntry {
  id: string
  timestamp: number
  level: LogLevel
  message: string
  source: string
  duration_ms?: number
  extra?: Record<string, unknown>
}
