<script setup lang="ts">
import { computed } from 'vue';
import { BookOpen } from 'lucide-vue-next';
import { groupSources, sourceKey, summarizeSources, type SourceSnippet } from './sources';
const props = defineProps<{ sources: SourceSnippet[]; expanded: boolean }>();
const emit = defineEmits<{ 'update:expanded': [value: boolean] }>();
const groups = computed(() => groupSources(props.sources));
const view = computed(() => summarizeSources(groups.value, props.expanded));
</script>
<template>
  <div v-if="sources.length" class="sources">
    <BookOpen :size="14" /><span>参考来源 · 当前任务</span>
    <details v-for="source in view.groups" :key="source.id">
      <summary>{{ source.name }} · {{ source.totalSnippets }} 个片段<span v-if="source.snippets.length < source.totalSnippets">（当前显示 {{ source.snippets.length }} 个）</span></summary>
      <p v-for="(snippet, i) in source.snippets" :key="sourceKey(snippet)"><strong>片段 {{ i + 1 }}</strong><br />{{ snippet.text }}</p>
    </details>
    <p v-if="view.truncated" class="source-summary">
      共 {{ groups.length }} 个来源、{{ view.totalSnippets }} 个片段<span v-if="view.hiddenGroups"> · 另有 {{ view.hiddenGroups }} 个来源</span><span v-if="view.hiddenSnippets"> · {{ view.hiddenSnippets }} 个片段</span>未显示
      <button class="text-button" @click="emit('update:expanded', true)">显示全部</button>
    </p>
    <p v-else-if="expanded && view.canCollapse" class="source-summary">
      已显示全部 {{ groups.length }} 个来源、{{ view.totalSnippets }} 个片段
      <button class="text-button" @click="emit('update:expanded', false)">收起</button>
    </p>
  </div>
</template>
