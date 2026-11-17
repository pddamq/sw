<script setup lang="ts">
import type { ModelCapability } from './models';
const props = defineProps<{ modelValue: Record<string, ModelCapability>; models: string[] }>();
const emit = defineEmits<{ 'update:modelValue': [value: Record<string, ModelCapability>] }>();
function update(id: string, patch: Partial<ModelCapability>) {
  emit('update:modelValue', { ...props.modelValue, [id]: { ...props.modelValue[id], ...patch } });
}
function effort(id: string, value: string) {
  update(id, { reasoning_efforts: value === 'unknown' ? null : value === 'none' ? [] : value.split(',') as ('low' | 'medium' | 'high')[] });
}
</script>
<template>
  <details class="capabilities"><summary>逐模型能力配置</summary>
    <p>未知能力保留手动选择；这些设置是你的声明，不是模型列表自动验证的结果。图片仍需开启通用视觉输入。</p>
    <fieldset v-for="id in models" :key="id"><legend>{{ id }}</legend>
      <label>支持的思考强度<select :value="modelValue[id]?.reasoning_efforts == null ? 'unknown' : modelValue[id]?.reasoning_efforts?.length ? modelValue[id]?.reasoning_efforts?.join(',') : 'none'" @change="effort(id, ($event.target as HTMLSelectElement).value)">
        <option value="unknown">未知（手动选择）</option><option value="none">仅默认</option><option value="low,medium,high">low / medium / high</option><option value="low,high">low / high</option><option value="high">high</option>
      </select></label>
      <label>图片输入<select :value="modelValue[id]?.vision == null ? 'unknown' : String(modelValue[id]?.vision)" @change="update(id, { vision: ($event.target as HTMLSelectElement).value === 'unknown' ? null : ($event.target as HTMLSelectElement).value === 'true' })">
        <option value="unknown">未知（遵循通用设置）</option><option value="true">支持</option><option value="false">不支持</option>
      </select></label>
      <label>上下文窗口覆盖<input :value="modelValue[id]?.context_window ?? ''" type="number" min="1" step="1" placeholder="留空继承服务设置" @input="update(id, { context_window: ($event.target as HTMLInputElement).value })" /></label>
      <label>输出上限覆盖<input :value="modelValue[id]?.max_output_tokens ?? ''" type="number" min="1" step="1" placeholder="留空继承服务设置" @input="update(id, { max_output_tokens: ($event.target as HTMLInputElement).value })" /></label>
    </fieldset>
  </details>
</template>
<style scoped>
.capabilities { border-top:1px solid var(--stroke); padding-top:12px; font-size:12px; }
summary { cursor:pointer; color:var(--muted); }
p { color:var(--muted); line-height:1.7; }
fieldset { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; border:1px solid var(--stroke); border-radius:6px; margin:12px 0; }
legend { overflow-wrap:anywhere; }
label { display:grid; gap:5px; color:var(--muted); }
input, select { min-width:0; width:100%; box-sizing:border-box; border:1px solid var(--stroke); border-radius:5px; padding:7px; background:var(--input); color:var(--text); }
@media (max-width:520px) { fieldset { grid-template-columns:1fr; } }
</style>
