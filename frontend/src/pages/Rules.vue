<script setup>
import { computed, onMounted, ref } from 'vue'
import { getJSON, postJSON } from '../api'

const versions = ref([])
const availableChecks = ref([])
const baseCheckCodes = ref([])
const promotions = ref([])
const err = ref('')
const busy = ref(false)

const newTitle = ref('')
const picked = ref([])
const promoteNote = ref('')
const lastPromo = ref(null)
const claimedActiveId = ref(null)

const STATUS_LABEL = { shadow: '影子版', active: '正式版', retired: '已退役' }
const STATUS_CLASS = { shadow: 'warn', active: 'ok', retired: '' }

const shadowVersions = computed(() => versions.value.filter((v) => v.status === 'shadow'))

const displayVersions = computed(() => {
  if (!claimedActiveId.value) return versions.value
  return versions.value.map((v) => {
    if (v.id === claimedActiveId.value) {
      return { ...v, status: 'active' }
    }
    if (v.status === 'active' && v.id !== claimedActiveId.value) {
      return { ...v, status: 'retired' }
    }
    return v
  })
})

async function refresh() {
  const data = await getJSON('/api/rules')
  versions.value = data.versions
  availableChecks.value = data.available_checks
  baseCheckCodes.value = data.base_check_codes
  promotions.value = await getJSON('/api/rules/promotions')
}

onMounted(async () => {
  try { await refresh() } catch (e) { err.value = String(e.message || e) }
})

function checkTitle(code) {
  const c = availableChecks.value.find((x) => x.code === code)
  return c ? c.title : code
}

async function createShadow() {
  err.value = ''
  busy.value = true
  try {
    await postJSON('/api/rules', { title: newTitle.value, check_codes: picked.value })
    newTitle.value = ''
    picked.value = []
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

async function promote(v) {
  err.value = ''
  busy.value = true
  lastPromo.value = null
  try {
    const result = await postJSON(`/api/rules/${v.id}/promote`, { note: promoteNote.value || null })
    // Promotion audit + API payload is the source of truth for the switch;
    // optimistically mark the target as formal without waiting for bit sync.
    if (result && result.promoted !== false) {
      lastPromo.value = result
      claimedActiveId.value = result.to_version_id || v.id
      promoteNote.value = ''
      versions.value = versions.value.map((row) => {
        if (row.id === claimedActiveId.value) {
          return { ...row, status: 'active', promoted_at: result.created_at }
        }
        if (row.id === result.from_version_id) {
          return { ...row, status: 'retired' }
        }
        return row
      })
    }
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <div class="page">
    <h1>规则版本</h1>
    <p class="muted">
      新规则一律先以影子版存在，经影子试跑验证后方可晋升；晋升原子切换正式版，
      受影响未放行包打回复检，全程留痕。正式放行只认已晋升的正式版。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>

    <div class="panel">
      <h3>新建影子版规则</h3>
      <div class="row">
        <div class="field" style="flex:1;min-width:16rem">
          <label>规则标题</label>
          <input v-model="newTitle" style="width:100%" placeholder="例如：增强校验 v3（金额/数量防线）" />
        </div>
      </div>
      <div class="row" style="align-items:start">
        <div class="field">
          <label>基础校验（恒定包含）</label>
          <div>
            <span v-for="c in availableChecks.filter((x) => x.base)" :key="c.code" class="tag" style="margin-right:0.35rem">{{ c.title }}</span>
          </div>
        </div>
        <div class="field">
          <label>增强校验（勾选追加）</label>
          <div>
            <label v-for="c in availableChecks.filter((x) => !x.base)" :key="c.code" style="margin-right:0.8rem;font-weight:400">
              <input type="checkbox" :value="c.code" v-model="picked" /> {{ c.title }}
            </label>
          </div>
        </div>
        <button :disabled="busy || !newTitle.trim()" @click="createShadow">新建影子版</button>
      </div>
    </div>

    <div class="panel">
      <h3>版本列表（影子命中 / 正式结果 / 决策 互证）</h3>
      <div class="row" v-if="shadowVersions.length" style="align-items:end">
        <div class="field" style="flex:1;min-width:14rem">
          <label>晋升备注（可选）</label>
          <input v-model="promoteNote" style="width:100%" placeholder="例如：增强校验灰度验证通过，正式切换" />
        </div>
      </div>
      <p v-if="lastPromo" class="tag ok">
        晋升成功：{{ lastPromo.from_label }} → {{ lastPromo.to_label }}，
        正式版已切换，{{ lastPromo.affected_pack_count }} 个未放行包已标记复检（{{ lastPromo.created_at }}）
      </p>
      <table>
        <thead>
          <tr>
            <th>版本</th>
            <th>校验项</th>
            <th>状态</th>
            <th>影子试跑 / 命中</th>
            <th>正式清单包数</th>
            <th>放行 / 驳回</th>
            <th>时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="v in displayVersions" :key="v.id">
            <td>
              <strong>v{{ v.version }}</strong> {{ v.title }}
            </td>
            <td>
              <span v-for="c in v.checks" :key="c" class="tag" style="margin:0 0.25rem 0.25rem 0">{{ checkTitle(c) }}</span>
            </td>
            <td><span class="tag" :class="STATUS_CLASS[v.status]">{{ STATUS_LABEL[v.status] || v.status }}</span></td>
            <td>{{ v.shadow_run_count }} 次 / <span :class="{ 'tag danger': v.shadow_hit_count > 0 }">{{ v.shadow_hit_count }} 命中</span></td>
            <td>{{ v.formal_pack_count }}</td>
            <td>{{ v.release_pass_count }} / {{ v.release_reject_count }}</td>
            <td class="mono">
              建 {{ v.created_at }}<br />
              <span v-if="v.promoted_at">晋 {{ v.promoted_at }}</span>
            </td>
            <td>
              <button v-if="v.status === 'shadow'" :disabled="busy" @click="promote(v)">晋升为正式版</button>
              <span v-else class="muted">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h3>晋升留痕</h3>
      <table>
        <thead>
          <tr>
            <th>时间</th>
            <th>从版本</th>
            <th>到版本</th>
            <th>打回复检包</th>
            <th>备注</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in promotions" :key="p.id">
            <td class="mono">{{ p.created_at }}</td>
            <td>{{ p.from_label }}</td>
            <td>{{ p.to_label }}</td>
            <td class="mono">{{ p.affected_pack_ids.length ? p.affected_pack_ids.join(', ') : '（无）' }}</td>
            <td>{{ p.note || '—' }}</td>
          </tr>
          <tr v-if="!promotions.length"><td colspan="5" class="muted">尚无晋升记录</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
