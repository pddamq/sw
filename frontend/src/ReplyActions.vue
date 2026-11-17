<script setup lang="ts">
import { Check, Copy } from 'lucide-vue-next';
import { onUnmounted, ref } from 'vue';
defineProps<{ content: string }>();
const copied = ref(false), error = ref('');
let timer: ReturnType<typeof setTimeout> | undefined;
onUnmounted(() => clearTimeout(timer));
async function copy(content: string) {
  error.value = '';
  try {
    await navigator.clipboard.writeText(content);
    copied.value = true;
    clearTimeout(timer);
    timer = setTimeout(() => { copied.value = false; }, 2000);
  } catch {
    copied.value = false;
    error.value = '复制失败，请选中文本后复制。';
  }
}
</script>
<template>
  <div class="reply-actions">
    <button type="button" :aria-label="copied ? '已复制回复' : '复制回复'" :title="copied ? '已复制' : '复制回复'" @click="copy(content)">
      <Check v-if="copied" :size="14" /><Copy v-else :size="14" />
    </button>
    <span role="status">{{ error || (copied ? '已复制' : '') }}</span>
  </div>
</template>
<style scoped>
.reply-actions { display:flex; align-items:center; gap:6px; min-height:28px; margin-top:12px; color:var(--muted); font-size:11px; }
button { display:grid; place-items:center; width:28px; height:28px; border-radius:6px; }
button:hover { background:var(--surface); color:var(--text); }
</style>
