<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue';
import { ChevronDown } from 'lucide-vue-next';
import { isAtBottom } from './scrollFollow';

const props = defineProps<{ feed?: HTMLElement; prompts: { id: string; content: string }[] }>();
const emit = defineEmits<{ bottom: []; jump: [id: string] }>();
const rail = ref<HTMLElement>();
const current = ref('');
const away = ref(false);
const links = computed(() => props.prompts.map((p, i) => ({ id: p.id, label: `第 ${i + 1} 条提问：${p.content.trim().replace(/\s+/g, ' ').slice(0, 80) || '图片消息'}` })));
let frame = 0;
function measure() {
  frame = 0;
  const feed = props.feed;
  if (!feed || !links.value.length) { current.value = ''; away.value = false; return; }
  away.value = !isAtBottom(feed);
  const top = feed.getBoundingClientRect().top + 12;
  let lo = 0, hi = links.value.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    const anchor = document.getElementById(`chat-message-${links.value[mid].id}`);
    if (anchor && anchor.getBoundingClientRect().top <= top) lo = mid + 1;
    else hi = mid;
  }
  current.value = links.value[away.value ? Math.max(0, lo - 1) : links.value.length - 1].id;
}
function schedule() { if (!frame) frame = requestAnimationFrame(measure); }
watch(() => [props.feed, props.prompts] as const, ([feed], _, cleanup) => {
  if (!feed) return;
  const observer = new ResizeObserver(schedule);
  observer.observe(feed);
  if (feed.firstElementChild) observer.observe(feed.firstElementChild);
  feed.addEventListener('scroll', schedule, { passive: true });
  schedule();
  cleanup(() => { observer.disconnect(); feed.removeEventListener('scroll', schedule); });
}, { immediate: true, flush: 'post' });
watch(current, async () => {
  await nextTick();
  const list = rail.value, mark = list?.querySelector<HTMLElement>('[aria-current="location"]');
  if (!list || !mark) return;
  const diff = mark.getBoundingClientRect().top - list.getBoundingClientRect().top;
  if (diff < 0) list.scrollTop += diff;
  else if (diff + mark.offsetHeight > list.clientHeight) list.scrollTop += diff + mark.offsetHeight - list.clientHeight;
});
onUnmounted(() => cancelAnimationFrame(frame));
</script>
<template>
  <nav v-if="links.length" ref="rail" class="prompt-rail" aria-label="用户消息导航">
    <button v-for="link in links" :key="link.id" type="button" :title="link.label" :aria-label="link.label" :aria-current="current === link.id ? 'location' : undefined" @click="emit('jump', link.id)"><span /></button>
  </nav>
  <button v-if="away && links.length" class="jump-bottom" type="button" title="返回最底部" aria-label="返回最底部" @click="emit('bottom')"><ChevronDown :size="17" /></button>
</template>
<style scoped>
.prompt-rail { position:absolute; z-index:1; right:6px; top:50%; transform:translateY(-50%); max-height:68%; overflow-y:auto; overscroll-behavior:contain; scrollbar-width:none; display:flex; flex-direction:column; align-items:flex-end; }
.prompt-rail::-webkit-scrollbar { display:none; }
.prompt-rail button { flex:none; display:grid; justify-items:end; align-items:center; width:25px; height:16px; padding:0 3px; border:0; background:transparent; cursor:pointer; }
.prompt-rail button span { height:2px; width:9px; border-radius:1px; background:var(--muted); opacity:.65; }
.prompt-rail button[aria-current] span,.prompt-rail button:hover span,.prompt-rail button:focus-visible span { width:18px; background:var(--text); opacity:1; }
.jump-bottom { position:absolute; bottom:12px; left:50%; transform:translateX(-50%); z-index:1; display:grid; place-items:center; width:34px; height:34px; border-radius:50%; border:1px solid var(--stroke); color:var(--text); background:var(--surface); box-shadow:0 2px 8px #0003; cursor:pointer; }
.jump-bottom:hover { color:var(--accent); }
.jump-bottom:focus-visible,.prompt-rail button:focus-visible { outline:2px solid var(--accent); outline-offset:-2px; }
</style>
