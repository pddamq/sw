<script setup lang="ts">
import { ArrowUp, Check, ChevronDown, Circle, Pause, Pencil, Play, Trash2, X } from 'lucide-vue-next';
import { nextTick, ref, watch } from 'vue';
import type { QueuedMessage } from './messageQueue';
const props = defineProps<{ items: QueuedMessage[]; paused: boolean; running: boolean; disabled?: boolean; feedback?: string }>();
const emit = defineEmits<{ remove: [id: string]; edit: [id: string, text: string]; send: [id: string]; pause: []; resume: [] }>();
const open = ref(true), editing = ref(''), draft = ref('');
const editor = ref<HTMLTextAreaElement>();
watch(() => props.items.map(item => item.id).join(','), () => {
  if (!props.items.some(item => item.id === editing.value)) editing.value = '';
});
async function startEdit(item: QueuedMessage) {
  emit('pause');
  editing.value = item.id; draft.value = item.text;
  await nextTick(); editor.value?.focus();
}
function save() { if (draft.value.trim()) { emit('edit', editing.value, draft.value); editing.value = ''; } }
</script>
<template>
  <section v-if="items.length" class="message-queue" aria-label="待发送消息队列">
    <div class="queue-heading">
      <button type="button" class="queue-toggle" :aria-expanded="open" @click="open = !open"><ChevronDown :size="13" :class="{ collapsed: !open }" />{{ items.length }} 条待发送消息<span v-if="paused">· 已暂停</span></button>
      <button v-if="paused" type="button" class="queue-control" :disabled="disabled || items.some(item => item.state === 'uncertain')" @click="emit('resume')"><Play :size="12" />继续队列</button>
      <button v-else type="button" class="queue-control" title="暂停后续消息，当前任务继续执行" @click="emit('pause')"><Pause :size="12" />暂停</button>
    </div>
    <ul v-if="open">
      <li v-for="item in items" :key="item.id" :class="{ uncertain: item.state === 'uncertain' }">
        <template v-if="editing === item.id">
          <textarea :ref="el => { editor = el as HTMLTextAreaElement }" v-model="draft" aria-label="编辑排队消息" maxlength="16000" rows="2" @keydown.esc="editing = ''" @keydown.enter="event => { if (!event.shiftKey && !event.isComposing) { event.preventDefault(); save(); } }" />
          <button type="button" class="queue-icon" aria-label="保存编辑" :disabled="!draft.trim()" @click="save"><Check :size="14" /></button>
          <button type="button" class="queue-icon" aria-label="取消编辑" @click="editing = ''"><X :size="14" /></button>
        </template>
        <template v-else>
          <Circle :size="12" class="queue-dot" />
      <span>{{ item.modelingBackend === 'blender' ? 'Blender' : 'SW' }}</span>
          <span class="queue-text" :title="item.text">{{ item.text }}</span>
          <span v-if="item.state === 'sending'" class="queue-state">正在发送…</span>
          <span v-else-if="item.state === 'steering'" class="queue-state">已接收 · 等待下一步</span>
          <span v-else-if="item.state === 'uncertain'" class="queue-state">待核实</span>
          <span v-else-if="!paused && item.id === items.find(row => row.state === 'pending')?.id" class="queue-state queue-next">下一条</span>
          <div class="queue-actions">
            <button v-if="item.state === 'pending'" type="button" class="queue-send" :disabled="disabled" :title="running ? '插入当前任务的下一步；当前模型请求及工具批次完成后读取，沿用当前模型' : '立即发送此消息'" @click="emit('send', item.id)">{{ running ? '插话发送' : '立即发送' }}<ArrowUp :size="12" /></button>
            <button v-if="item.state === 'pending'" type="button" class="queue-icon" :aria-label="'编辑消息 ' + item.text" @click="startEdit(item)"><Pencil :size="13" /></button>
            <button type="button" class="queue-icon" :disabled="item.state === 'sending' || item.state === 'steering'" :aria-label="'删除排队消息 ' + item.text" @click="emit('remove', item.id)"><Trash2 :size="13" /></button>
          </div>
        </template>
      </li>
    </ul>
    <p v-if="feedback" class="queue-feedback" role="status" aria-live="polite">{{ feedback }}</p>
    <p v-if="items.some(item => item.state === 'uncertain')" role="status">有消息的发送结果待核实。请先检查会话记录，再删除该排队项；队列不会自动重发。</p>
    <p v-else-if="paused">队列已暂停，点击继续后按顺序发送。排队消息使用加入时选定的模型。</p>
  </section>
</template>
<style scoped>
.message-queue { margin:0 0 8px; padding:7px 9px; border:1px solid var(--stroke); border-radius:9px; background:var(--panel); font-size:12px; min-width:0; }
.queue-heading { display:flex; align-items:center; justify-content:space-between; gap:8px; color:var(--muted); }
.queue-toggle, .queue-control { display:inline-flex; align-items:center; gap:5px; font-size:11px; padding:2px 0; }
.queue-toggle svg { transition:transform .15s; }
.queue-toggle svg.collapsed { transform:rotate(-90deg); }
ul { list-style:none; padding:0; margin:5px 0 0; max-height:220px; overflow:auto; }
li { display:flex; align-items:center; gap:7px; min-height:29px; padding:3px 5px; border-radius:5px; }
li:hover, li:focus-within { background:var(--raised); }
.queue-dot { color:var(--muted); flex-shrink:0; }
.queue-text { flex:1; min-width:0; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }
.queue-actions { display:flex; align-items:center; gap:3px; flex-shrink:0; }
.queue-icon { display:grid; place-items:center; width:24px; height:24px; border-radius:4px; color:var(--muted); }
.queue-send { display:flex; align-items:center; gap:3px; font-size:10px; color:var(--muted); padding:4px; }
.queue-icon:hover, .queue-send:hover { color:var(--text); background:var(--surface); }
.queue-state { color:var(--muted); font-size:10px; white-space:nowrap; }
.queue-next, .queue-feedback { color:var(--accent); }
.uncertain .queue-state { color:var(--danger); }
p { margin:7px 5px 2px; color:var(--muted); font-size:11px; line-height:1.6; }
textarea { flex:1; min-width:0; resize:vertical; max-height:160px; border:1px solid var(--stroke); border-radius:4px; padding:6px; background:var(--input); color:var(--text); font:inherit; }
@media (hover:hover) { .queue-actions { opacity:.4; } li:hover .queue-actions, li:focus-within .queue-actions { opacity:1; } }
@media (prefers-reduced-motion:reduce) { .queue-toggle svg { transition:none; } }
</style>
