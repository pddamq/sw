<script setup lang="ts">
import { ChevronRight, Image } from 'lucide-vue-next';
import { defineAsyncComponent, ref } from 'vue';
import type { MediaItem } from './media';

// Hidden images are mounted only when requested, avoiding downloads for closed history.
const MediaImage = defineAsyncComponent(() => import('./MediaImage.vue'));
defineProps<{ images: MediaItem[]; name: string }>();
const open = ref(false);
function onToggle(event: Event) {
  open.value = (event.currentTarget as HTMLDetailsElement).open;
}
</script>
<template>
  <details v-if="images.length" class="tool-image-block" @toggle="onToggle">
    <summary>
      <Image :size="13" aria-hidden="true" />
      <span>工具图片 · {{ images.length }} 张</span>
      <code>{{ name }}</code>
      <ChevronRight :size="12" class="expand-icon" aria-hidden="true" />
    </summary>
    <div v-if="open" class="tool-image-content">
      <p>图片用于视觉参考，工程尺寸以测量结果为准。</p>
      <MediaImage v-for="image in images" :key="image.id" :image="image" />
    </div>
  </details>
</template>
<style scoped>
.tool-image-block { min-width:0; margin:5px 0 12px; color:var(--muted); font-size:12px; }
summary { display:flex; align-items:center; gap:7px; padding:5px 0; cursor:pointer; list-style:none; }
summary::-webkit-details-marker { display:none; }
summary:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
summary > svg { flex-shrink:0; }
summary code { margin-left:auto; font-size:10px; overflow-wrap:anywhere; }
.tool-image-block[open] .expand-icon { transform:rotate(90deg); }
.tool-image-content { display:grid; gap:8px; min-width:0; margin:4px 0 0 20px; }
.tool-image-content p { margin:0; font-size:11px; }
</style>
