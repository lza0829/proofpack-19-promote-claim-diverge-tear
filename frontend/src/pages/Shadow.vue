<script setup>
import { computed, onMounted, ref } from 'vue'
import { getJSON, postJSON } from '../api'

const packs = ref([])
const shadowVersions = ref([])
const packId = ref(null)
const versionId = ref(null)
const note = ref('')
const last = ref(null)
const shadowRuns = ref([])
const formalResults = ref([])
const err = ref('')
const busy = ref(false)

const PACK_STATUS = { built: '已生成', checked_ok: '校验通过', checked_fail: '校验未过', released: '已放行', rejected: '已驳回' }

const selectedPack = computed(() => packs.value.find((p) => p.id === Number(packId.value)))

async function refresh() {
  packs.value = await getJSON('/api/packs')
  const rules = await getJSON('/api/rules')
  shadowVersions.value = rules.versions.filter((v) => v.status === 'shadow')
  if (!packId.value && packs.value.length) packId.value = packs.value[0].id
  if (!versionId.value && shadowVersions.value.length) versionId.value = shadowVersions.value[0].id
  await loadTracks()
}

async function loadTracks() {
  if (!packId.value) return
  shadowRuns.value = await getJSON(`/api/shadow?pack_id=${packId.value}`)
  formalResults.value = await getJSON(`/api/checklist?pack_id=${packId.value}`)
}

onMounted(async () => {
  try { await refresh() } catch (e) { err.value = String(e.message || e) }
})

async function run() {
  err.value = ''
  busy.value = true
  last.value = null
  try {
    last.value = await postJSON('/api/shadow/run', {
      pack_id: Number(packId.value),
      rule_version_id: Number(versionId.value),
      note: note.value || null,
    })
    note.value = ''
    await loadTracks()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

async function onPackChange() {
  last.value = null
  await loadTracks()
}
</script>
<template>
  <div class="page">
    <h1>影子试跑</h1>
    <p class="muted">
      影子版规则对指定证据包只读试跑，结果只落影子轨：可查、可对照，
      但不进正式清单表、不动包状态、不挡正式放行。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>

    <div class="panel">
      <h3>发起试跑</h3>
      <div class="row">
        <div class="field">
          <label>证据包</label>
          <select v-model="packId" @change="onPackChange">
            <option v-for="p in packs" :key="p.id" :value="p.id">
              {{ p.pack_no }}（{{ p.item_count }} 条 · {{ PACK_STATUS[p.status] || p.status }}）
            </option>
          </select>
        </div>
        <div class="field">
          <label>影子版规则</label>
          <select v-model="versionId">
            <option v-for="v in shadowVersions" :key="v.id" :value="v.id">{{ v.label }}</option>
          </select>
        </div>
        <div class="field" style="flex:1;min-width:12rem">
          <label>备注（可选）</label>
          <input v-model="note" style="width:100%" placeholder="例如：上线前灰度验证" />
        </div>
        <button :disabled="busy || !packId || !versionId" @click="run">发起影子试跑</button>
      </div>
      <p v-if="!shadowVersions.length" class="muted">当前无影子版规则，请先到「规则版本」页新建。</p>
      <p v-if="last">
        试跑结果
        <span class="tag" :class="last.status === 'passed' ? 'ok' : 'danger'">
          {{ last.status === 'passed' ? '全部通过' : `命中 ${last.hit_count} 项` }}
        </span>
        · {{ last.rule_label }} · <span class="mono">{{ last.created_at }}</span>
      </p>
    </div>

    <div class="panel">
      <h3>影子轨（仅影子结果，晋升前不落正式清单表）</h3>
      <table>
        <thead>
          <tr>
            <th>时间</th>
            <th>规则版本</th>
            <th>结果</th>
            <th>命中数</th>
            <th>逐项明细</th>
            <th>备注</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in shadowRuns" :key="r.id">
            <td class="mono">{{ r.created_at }}</td>
            <td>{{ r.rule_label }}</td>
            <td>
              <span class="tag" :class="r.status === 'passed' ? 'ok' : 'danger'">
                {{ r.status === 'passed' ? '通过' : '未过' }}
              </span>
            </td>
            <td>{{ r.hit_count }}</td>
            <td>
              <div v-for="c in r.results" :key="c.check_code">
                <span class="tag" :class="c.passed ? 'ok' : 'danger'">{{ c.passed ? '过' : '未过' }}</span>
                {{ c.check_title }}：<span class="muted">{{ c.detail }}</span>
              </div>
            </td>
            <td>{{ r.note || '—' }}</td>
          </tr>
          <tr v-if="!shadowRuns.length"><td colspan="6" class="muted">该包暂无影子试跑记录</td></tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h3>正式轨（只读对照 · 正式放行只读此轨）</h3>
      <p class="muted" v-if="selectedPack">
        包状态：<span class="tag">{{ PACK_STATUS[selectedPack.status] || selectedPack.status }}</span>
      </p>
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
          <tr v-for="r in formalResults" :key="r.id">
            <td>{{ r.check_title }}</td>
            <td>
              <span class="tag" :class="r.passed ? 'ok' : 'danger'">{{ r.passed ? '通过' : '未通过' }}</span>
            </td>
            <td>{{ r.detail }}</td>
            <td class="mono">规则版本 #{{ r.rule_version_id ?? '—' }}</td>
            <td class="mono">{{ r.created_at }}</td>
          </tr>
          <tr v-if="!formalResults.length"><td colspan="5" class="muted">该包尚未跑正式校验清单</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
