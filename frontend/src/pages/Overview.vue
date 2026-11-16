<script setup>
import { onMounted, ref } from 'vue'
import { getJSON } from '../api'
const stats = ref(null)
const err = ref('')
onMounted(async () => {
  try { stats.value = await getJSON('/api/dashboard') } catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <h1>总览</h1>
    <p class="muted">采样抽取 → 证据打包 → 校验清单 → 放行/驳回闭环。清单全过才可放行；新规则先影子试跑，晋升后才进正式闸。</p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel" v-if="stats">
      <h3>主链</h3>
      <div class="stat-grid">
        <div class="stat"><div class="stat-num">{{ stats.subject_count }}</div><div class="stat-label">举证对象</div></div>
        <div class="stat"><div class="stat-num">{{ stats.enabled_subjects }}</div><div class="stat-label">已启用</div></div>
        <div class="stat"><div class="stat-num">{{ stats.sample_batch_count }}</div><div class="stat-label">采样批次</div></div>
        <div class="stat"><div class="stat-num">{{ stats.pack_count }}</div><div class="stat-label">证据包</div></div>
        <div class="stat"><div class="stat-num">{{ stats.checklist_count }}</div><div class="stat-label">清单结果</div></div>
        <div class="stat"><div class="stat-num">{{ stats.passed_releases }}</div><div class="stat-label">已放行</div></div>
        <div class="stat"><div class="stat-num">{{ stats.rejected_releases }}</div><div class="stat-label">已驳回</div></div>
      </div>
    </div>
    <div class="panel" v-if="stats">
      <h3>规则版本与影子试跑（互证）</h3>
      <div class="stat-grid">
        <div class="stat"><div class="stat-num" style="font-size:1.05rem">{{ stats.active_rule_label || '—' }}</div><div class="stat-label">当前正式规则</div></div>
        <div class="stat"><div class="stat-num">{{ stats.shadow_version_count }}</div><div class="stat-label">影子版规则</div></div>
        <div class="stat"><div class="stat-num">{{ stats.shadow_run_count }}</div><div class="stat-label">影子试跑</div></div>
        <div class="stat"><div class="stat-num">{{ stats.shadow_hit_count }}</div><div class="stat-label">影子命中</div></div>
        <div class="stat"><div class="stat-num">{{ stats.promotion_count }}</div><div class="stat-label">晋升次数</div></div>
      </div>
      <p style="margin-top:1rem"><router-link to="/sample">去采样台跑一趟 →</router-link></p>
    </div>
  </div>
</template>
