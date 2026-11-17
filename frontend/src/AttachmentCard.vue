<script setup lang="ts">
import { FileText } from 'lucide-vue-next';
import MediaImage from './MediaImage.vue';
import type { MediaItem } from './media';
defineProps<{ image: MediaItem; compact?: boolean }>();
</script>
<template>
  <div v-if="image.kind === 'document'" class="document-attachment" :title="image.name">
    <FileText :size="22" />
    <div><strong>{{ image.name }}</strong><small>文本已提取 · {{ Math.max(1, Math.ceil(image.size / 1024)) }} KB</small></div>
  </div>
  <MediaImage v-else :image="image" :compact="compact" />
</template>
<style scoped>
.document-attachment { display:flex; align-items:center; gap:10px; max-width:270px; padding:13px 32px 13px 12px; border:1px solid var(--stroke); border-radius:8px; background:var(--surface); color:var(--text); }
.document-attachment > svg { flex-shrink:0; color:var(--accent); }
.document-attachment > div { display:grid; gap:5px; min-width:0; }
.document-attachment strong { font-size:11px; font-weight:500; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.document-attachment small { font-size:10px; color:var(--muted); }
</style>
