<script setup lang="ts">
import ChunkingConfig from '@/components/ChunkingConfig.vue'
</script>

<template>
  <div class="app-container">
    <header class="app-header">
      <h1>RAG 文档分块系统</h1>
      <p class="subtitle">支持多种高级分块策略 · 实时预览 · 企业级架构</p>
    </header>
    <main>
      <ChunkingConfig />
    </main>
  </div>
</template>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif;
  background: #f5f7fa;
  color: #303133;
}

.app-container {
  min-height: 100vh;
}

.app-header {
  background: linear-gradient(135deg, #409eff 0%, #337ecc 100%);
  color: white;
  padding: 24px 32px;
  text-align: center;
}

.app-header h1 {
  font-size: 28px;
  font-weight: 700;
}

.subtitle {
  margin-top: 8px;
  font-size: 14px;
  opacity: 0.85;
}
</style>
