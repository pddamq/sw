<script setup lang="ts">
import { FileText, FolderOpen, Trash2 } from 'lucide-vue-next';
import type { KnowledgeDocument, KnowledgeFolder } from './knowledgeLibrary';

defineProps<{ folder: KnowledgeFolder; disabled: boolean }>();
const emit = defineEmits<{ delete: [document: KnowledgeDocument] }>();
</script>

<template>
  <div class="knowledge-tree">
    <details v-for="child in folder.folders" :key="child.path" class="knowledge-folder" open>
      <summary :title="child.path"><FolderOpen :size="17" />{{ child.name }}</summary>
      <KnowledgeTree :folder="child" :disabled="disabled" @delete="emit('delete', $event)" />
    </details>
    <div v-for="doc in folder.documents" :key="doc.id" class="knowledge-file">
      <span class="knowledge-file-name" :title="doc.name"><FileText :size="17" />{{ doc.name.split('/').pop() }}</span>
      <span class="muted small">{{ doc.chunks }} 分块 · {{ new Date(doc.created_at).toLocaleDateString() }}</span>
      <button class="icon-button" :disabled="disabled" :aria-label="`删除文档 ${doc.name}`" :title="`删除 ${doc.name}`" @click="emit('delete', doc)">
        <Trash2 :size="15" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.knowledge-tree { min-width: 0; }
.knowledge-folder { padding: 5px 0; }
.knowledge-folder > summary { cursor: pointer; padding: 9px 4px; overflow-wrap: anywhere; }
.knowledge-folder > summary svg { vertical-align: middle; margin: 0 8px 0 4px; }
.knowledge-folder > .knowledge-tree { margin-left: 18px; padding-left: 12px; border-left: 1px solid var(--border, #5555); }
.knowledge-file { display: flex; align-items: center; gap: 12px; padding: 9px 4px; }
.knowledge-file-name { display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0; overflow-wrap: anywhere; }
.knowledge-file-name svg { flex-shrink: 0; }
@media (max-width: 640px) { .knowledge-file { flex-wrap: wrap; } }
</style>
