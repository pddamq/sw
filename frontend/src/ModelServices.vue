<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue';
import { ChevronDown, Plus, Server, X } from 'lucide-vue-next';
import { request } from './api';
import { modelProtocols, type ModelCatalog, type ModelProfile } from './models';
import ModelServiceEditor from './ModelServiceEditor.vue';
const props = defineProps<{ catalog: ModelCatalog; locked: boolean }>();
const emit = defineEmits<{ changed: [catalog: ModelCatalog] }>();
const NEW_SERVICE = '$new';
const firstSetup = !props.catalog.profiles.some(profile => profile.has_api_key)
  ? props.catalog.profiles[0]?.id || NEW_SERVICE : undefined;
const opened = ref<string | undefined>(firstSetup);
const visited = ref(new Set(firstSetup ? [firstSetup] : []));
const busyEditors = ref(new Set<string>()), deleting = ref(false), deleteTarget = ref<ModelProfile>();
const notice = ref(''), failure = ref('');
const busy = computed(() => deleting.value || busyEditors.value.size > 0);
let deleteController: AbortController | undefined;
onBeforeUnmount(() => deleteController?.abort());
function host(url: string) { try { return new URL(url).host; } catch { return url; } }
function protocolLabel(profile: ModelProfile) { return modelProtocols.find(p => p.value === (profile.protocol || 'chat'))?.label || 'Chat Completions'; }
function credentialLabel(profile: ModelProfile) {
  return profile.credential_unreadable ? '密钥需重新填写' : profile.has_api_key ? '密钥已配置' : '尚未配置密钥';
}
function toggle(id: string) {
  if (busy.value) return;
  deleteTarget.value = undefined; failure.value = ''; notice.value = '';
  visited.value.add(id); opened.value = opened.value === id ? undefined : id;
}
function setBusy(id: string, value: boolean) { if (value) busyEditors.value.add(id); else busyEditors.value.delete(id); }
function saved(id: string, data: ModelCatalog) {
  opened.value = undefined; visited.value.delete(id); busyEditors.value.delete(id);
  emit('changed', data); notice.value = '模型服务已保存，可在对话输入区选择。';
}
function cancel(id: string) { opened.value = undefined; visited.value.delete(id); }
async function remove() {
  const target = deleteTarget.value;
  if (!target || props.locked || busy.value) return;
  deleting.value = true; failure.value = ''; deleteController = new AbortController();
  try {
    const data = await request<ModelCatalog>(`/model/profiles/${target.id}`, { method: 'DELETE', signal: deleteController.signal });
    if (deleteController.signal.aborted) return;
    visited.value.delete(target.id);
    if (opened.value === target.id) opened.value = undefined;
    deleteTarget.value = undefined; emit('changed', data); notice.value = `服务“${target.name}”已删除。`;
  } catch (e) { if (!deleteController.signal.aborted) failure.value = e instanceof Error ? e.message : String(e); }
  finally { deleting.value = false; }
}
</script>
<template>
  <section class="model-panel" aria-label="模型服务配置">
    <header class="models-heading"><h3>模型服务</h3><p>连接自己的模型 API，为每个服务配置协议、密钥和可用模型。</p></header>
    <p v-if="locked" class="section-notice">任务运行中，模型配置暂时只读。</p>
    <p v-if="notice" class="saved-notice" role="status" aria-live="polite">{{ notice }}</p>
    <ul class="provider-cards" aria-label="已配置的模型服务">
      <li v-for="profile in catalog.profiles" :key="profile.id" class="provider-card" :class="{ expanded: opened === profile.id }">
        <div class="provider-header">
          <div class="provider-identity"><Server :size="17" aria-hidden="true" /><div><div class="provider-title"><strong>{{ profile.name }}</strong><span class="credential-dot" :class="{ configured: profile.has_api_key && !profile.credential_unreadable }" role="img" :aria-label="credentialLabel(profile)" :title="credentialLabel(profile)"></span><span v-if="profile.id === catalog.selected_id" class="current-tag">当前使用</span></div><p class="provider-meta"><span>{{ protocolLabel(profile) }}</span><span>{{ profile.models.length }} 个模型</span><span :title="profile.base_url">{{ host(profile.base_url) }}</span></p></div></div>
          <div class="provider-actions"><button type="button" class="row-button" :aria-label="'编辑服务 ' + profile.name" :aria-expanded="opened === profile.id" :aria-controls="'service-editor-' + profile.id" :disabled="busy" @click="toggle(profile.id)">{{ opened === profile.id ? '收起' : '编辑' }}<ChevronDown :size="12" :class="{ rotated: opened === profile.id }" /></button><button type="button" class="row-button danger" :aria-label="'删除服务 ' + profile.name" :disabled="locked || busy" @click="deleteTarget = profile; failure = ''">删除</button></div>
        </div>
        <div v-if="deleteTarget?.id === profile.id" class="delete-confirmation" role="group" :aria-label="'确认删除服务 ' + profile.name"><p>删除“{{ profile.name }}”？此服务保存的密钥和模型列表将一并移除。</p><div><button type="button" class="row-button" :disabled="deleting" @click="deleteTarget = undefined; failure = ''">取消删除</button><button type="button" class="row-button danger" :disabled="locked || busy" @click="remove">{{ deleting ? '删除中…' : '确认删除' }}</button></div><p v-if="failure" class="error" role="alert">{{ failure }}</p></div>
        <div :id="'service-editor-' + profile.id" v-show="opened === profile.id"><ModelServiceEditor v-if="visited.has(profile.id)" :profile="profile" :selected-model="catalog.selected_model" :locked="locked || deleting" :active="opened === profile.id" @saved="saved(profile.id, $event)" @cancel="cancel(profile.id)" @busy="setBusy(profile.id, $event)" /></div>
      </li>
    </ul>
    <div v-if="opened === NEW_SERVICE || visited.has(NEW_SERVICE)" v-show="opened === NEW_SERVICE" class="provider-card add-card">
      <header class="new-provider-heading"><div><h4>添加自定义 API</h4><p>支持 Chat Completions、Responses 和 Claude Messages。</p></div><button type="button" class="row-button" aria-label="收起添加服务" :disabled="busy" @click="toggle(NEW_SERVICE)"><X :size="16" /></button></header>
      <ModelServiceEditor :selected-model="''" :locked="locked || deleting" :active="opened === NEW_SERVICE" @saved="saved(NEW_SERVICE, $event)" @cancel="cancel(NEW_SERVICE)" @busy="setBusy(NEW_SERVICE, $event)" />
    </div>
    <button v-if="opened !== NEW_SERVICE" type="button" class="add-service" :disabled="locked || busy" @click="toggle(NEW_SERVICE)"><Plus :size="15" />添加服务</button>
    <p class="section-footnote">配置独立保存。密钥状态仅表示已配置，连接与工具能力请在服务内测试。</p>
  </section>
</template>
<style scoped>
.model-panel { display:flex; flex-direction:column; gap:12px; width:100%; max-width:720px; margin:0 auto; color:var(--text); }
.model-panel .models-heading h3 { font-size:16px; line-height:24px; font-weight:500; margin:0 0 5px; }
.model-panel p { margin:0; line-height:1.65; overflow-wrap:anywhere; }
.models-heading p { font-size:13px; color:var(--muted); }
.provider-cards { list-style:none; padding:0; margin:8px 0 0; display:flex; flex-direction:column; gap:10px; }
.provider-cards:empty { display:none; }
.provider-card { min-width:0; border:1px solid var(--stroke); border-radius:12px; background:var(--panel); padding:13px 14px; }
.provider-header { display:flex; gap:12px; align-items:center; }
.provider-identity { display:flex; gap:10px; align-items:center; min-width:0; flex:1; }
.provider-identity > svg { flex-shrink:0; color:var(--muted); }
.provider-identity > div { min-width:0; }
.provider-title { display:flex; flex-wrap:wrap; align-items:center; gap:7px; }
.provider-title strong { font-size:14px; font-weight:500; overflow-wrap:anywhere; }
.credential-dot { width:7px; height:7px; border-radius:50%; background:var(--danger); flex-shrink:0; }
.credential-dot.configured { background:var(--accent); }
.current-tag { font-size:10px; color:var(--muted); border:1px solid var(--stroke); padding:1px 5px; border-radius:4px; white-space:nowrap; }
.model-panel .provider-meta { display:flex; flex-wrap:wrap; column-gap:10px; margin-top:5px; font-size:11px; color:var(--muted); }
.provider-meta span { overflow-wrap:anywhere; }
.provider-actions { display:flex; gap:4px; align-items:center; flex-shrink:0; }
.row-button { display:inline-flex; align-items:center; justify-content:center; gap:5px; min-height:28px; padding:4px 9px; font-size:12px; border:1px solid var(--stroke); border-radius:6px; color:var(--text); background:transparent; }
.row-button.danger { border-color:transparent; color:var(--danger); }
.row-button:hover:not(:disabled), .add-service:hover:not(:disabled) { background:var(--surface); }
.row-button:focus-visible, .add-service:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.model-panel button:disabled { opacity:.45; cursor:default; }
.rotated { transform:rotate(180deg); }
.expanded .provider-header { margin-bottom:14px; }
.add-service { align-self:flex-start; display:inline-flex; align-items:center; justify-content:center; gap:6px; min-height:36px; padding:8px 14px; border:1px solid var(--stroke); border-radius:8px; color:var(--text); font-size:13px; }
.new-provider-heading { display:flex; gap:12px; align-items:flex-start; justify-content:space-between; margin-bottom:14px; }
.new-provider-heading h4 { margin:0 0 5px; font-size:14px; font-weight:500; }
.new-provider-heading p { color:var(--muted); font-size:12px; }
.section-footnote, .section-notice { color:var(--muted); font-size:11px; }
.saved-notice { color:var(--accent); font-size:12px; }
.delete-confirmation { margin-top:12px; padding:12px; border:1px solid var(--stroke); border-radius:8px; background:var(--surface); font-size:12px; }
.delete-confirmation > div { display:flex; gap:8px; justify-content:flex-end; margin-top:10px; }
.error { margin-top:8px!important; color:var(--danger); }
@media (max-width:520px) { .provider-card { padding:12px 10px; } .provider-header { flex-wrap:wrap; } .provider-identity { flex-basis:100%; } .provider-actions { margin-left:auto; } }
</style>
