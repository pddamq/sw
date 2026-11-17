<script setup lang="ts">
import { ref, watch, onUnmounted, nextTick } from "vue";
import { LoaderCircle, ImageOff, Maximize2, X } from "lucide-vue-next";
import { requestMedia } from "./api";
import type { MediaItem } from "./media";
const props = defineProps<{ image: MediaItem; compact?: boolean }>();
const src = ref(""),
  error = ref(""),
  expanded = ref(false);
let controller: AbortController | null = null;
const lightbox = ref<HTMLDivElement>(),
  trigger = ref<HTMLButtonElement>();
watch(expanded, async (value) => {
  await nextTick();
  if (value) lightbox.value?.focus();
  else trigger.value?.focus();
});
watch(
  () => props.image.id,
  async (id) => {
    controller?.abort();
    if (src.value) URL.revokeObjectURL(src.value);
    src.value = "";
    error.value = "";
    const current = new AbortController();
    controller = current;
    try {
      const blob = await requestMedia(id, current.signal);
      if (!current.signal.aborted) src.value = URL.createObjectURL(blob);
    } catch (e) {
      if (!current.signal.aborted)
        error.value = e instanceof Error ? e.message : String(e);
    }
  },
  { immediate: true },
);
onUnmounted(() => {
  controller?.abort();
  if (src.value) URL.revokeObjectURL(src.value);
});
</script>
<template>
  <figure class="media-card" :class="{ compact }">
    <button
      v-if="src"
      type="button"
      class="media-open"
      ref="trigger"
      :aria-label="'放大图片 ' + image.name"
      :title="`${image.name} · ${image.width} × ${image.height} · 点击放大`"
      @click="expanded = true"
    >
      <img :src="src" :alt="image.name" /><Maximize2 :size="14" />
    </button>
    <div v-else class="media-loading">
      <ImageOff v-if="error" :size="18" /><LoaderCircle
        v-else
        class="spin"
        :size="18"
      /><span>{{ error || "正在读取图片" }}</span>
    </div>
  </figure>
  <Teleport to="body"
    ><div
      v-if="expanded && src"
      class="image-lightbox"
      ref="lightbox"
      role="dialog"
      aria-modal="true"
      :aria-label="image.name"
      tabindex="-1"
      @click.self="expanded = false"
      @keydown.esc="expanded = false"
    >
      <button
        class="image-close"
        type="button"
        aria-label="关闭图片"
        @click="expanded = false"
      >
        <X :size="24" /></button
      ><img :src="src" :alt="image.name" @click="expanded = false" />
      <p>
        {{ image.name }} · {{ image.width }} × {{ image.height
        }}<br />图片仅用于视觉参考，工程尺寸以测量结果为准。
      </p>
    </div></Teleport
  >
</template>
