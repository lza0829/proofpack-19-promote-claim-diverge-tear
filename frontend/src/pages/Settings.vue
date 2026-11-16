<script setup>
import { onMounted, ref } from 'vue'
import { getJSON } from '../api'

const LABEL = {
  default_sample_rate: '默认采样率',
  require_checklist_pass: '放行须清单全过',
  sample_mode: '采样模式',
}

function displayValue(k, v) {
  if (k === 'require_checklist_pass') {
    return v === '1' || v === 1 || v === true || v === 'true' ? '是' : '否'
  }
  if (k === 'sample_mode') {
    return ({ deterministic: '确定性抽样', random: '随机抽样' })[v] || v
  }
  return v
}

const settings = ref({})
const err = ref('')
onMounted(async () => {
  try { settings.value = await getJSON('/api/settings') } catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <h1>设置</h1>
    <p class="muted">底座策略只读展示；影子试跑与规则版本见「影子试跑」「规则版本」页。</p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>配置项</th>
            <th>取值</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(v, k) in settings" :key="k">
            <td>{{ LABEL[k] || k }}</td>
            <td><span class="tag">{{ displayValue(k, v) }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
