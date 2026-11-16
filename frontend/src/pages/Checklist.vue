<script setup>
import { computed, onMounted, ref } from 'vue'
import { getJSON, postJSON } from '../api'

const packs = ref([])
const packId = ref(null)
const result = ref(null)
const history = ref([])
const activeRule = ref(null)
const err = ref('')
const busy = ref(false)

const stale = computed(() => {
  if (!activeRule.value || !history.value.length) return false
  return history.value.some((r) => r.rule_version_id !== activeRule.value.id)
})

async function refresh() {
  packs.value = await getJSON('/api/packs')
  const rules = await getJSON('/api/rules')
  activeRule.value = rules.versions.find((v) => v.status === 'active') || null
  if (!packId.value && packs.value.length) packId.value = packs.value[0].id
  if (packId.value) {
    history.value = await getJSON(`/api/checklist?pack_id=${packId.value}`)
  }
}

onMounted(async () => {
  try { await refresh() } catch (e) { err.value = String(e.message || e) }
})

async function run() {
  err.value = ''
  busy.value = true
  try {
    result.value = await postJSON('/api/checklist/run', { pack_id: Number(packId.value) })
    history.value = result.value.results || []
    packs.value = await getJSON('/api/packs')
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

async function onPackChange() {
  history.value = await getJSON(`/api/checklist?pack_id=${packId.value}`)
  result.value = null
}
</script>
<template>
  <div class="page">
    <h1>校验清单</h1>
    <p class="muted">
      正式轨：按当前已晋升的正式规则执行，结果与包内条目互证。
      <span v-if="activeRule">当前正式规则：<strong>{{ activeRule.label }}</strong></span>
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel">
      <h3>执行校验</h3>
      <div class="row">
        <div class="field">
          <label>证据包</label>
          <select v-model="packId" @change="onPackChange">
            <option v-for="p in packs" :key="p.id" :value="p.id">
              {{ p.pack_no }}（{{ p.item_count }} 条 · {{ ({ built: '已生成', checked_ok: '校验通过', checked_fail: '校验未过', released: '已放行', rejected: '已驳回' })[p.status] || p.status }}）
            </option>
          </select>
        </div>
        <button :disabled="busy || !packId" @click="run">跑校验清单</button>
      </div>
      <p v-if="result">
        条目数 <strong>{{ result.item_count }}</strong>
        · 依据 <strong>{{ result.rule_label }}</strong>
        ·
        <span class="tag" :class="result.all_passed ? 'ok' : 'danger'">
          {{ result.all_passed ? '全部通过' : '存在未通过项' }}
        </span>
      </p>
      <p v-if="stale" class="tag warn">
        以下结果基于旧规则版本，规则已晋升，须复检后方可放行
      </p>
    </div>
    <div class="panel">
      <h3>清单结果（正式轨）</h3>
      <table>
        <thead>
          <tr>
            <th>校验项</th>
            <th>结果</th>
            <th>说明</th>
            <th>规则版本</th>
            <th>时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in history" :key="r.id || r.check_code">
            <td>{{ r.check_title }}</td>
            <td>
              <span class="tag" :class="r.passed ? 'ok' : 'danger'">
                {{ r.passed ? '通过' : '未通过' }}
              </span>
            </td>
            <td>{{ r.detail }}</td>
            <td class="mono">
              <span :class="{ 'tag warn': activeRule && r.rule_version_id !== activeRule.id }">
                #{{ r.rule_version_id ?? '—' }}
              </span>
            </td>
            <td class="mono">{{ r.created_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
