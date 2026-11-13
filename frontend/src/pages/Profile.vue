<script setup>
import { computed, onMounted, ref } from 'vue'
import { getJSON, postJSON, patchJSON } from '../api'

const datasets = ref([])
const datasetId = ref(null)
const config = ref(null)
const baselines = ref([])
const batches = ref([])
const err = ref('')
const msg = ref('')
const newEntry = ref({ field: '', metric: 'null_rate', baseline_value: 0, threshold: 0.1, threshold_mode: 'abs', enabled: true })

const METRIC_LABEL = { null_rate: '空值率', distinct_rate: '去重率', mean: '均值' }
const blockedBatches = computed(() =>
  batches.value.filter(b => b.status === 'profile_blocked' && b.report?.profile_gate)
)

async function loadGate() {
  if (!datasetId.value) return
  const data = await getJSON(`/api/profile-gate/${datasetId.value}`)
  config.value = data.config
  baselines.value = data.baselines
}
async function loadAll() {
  datasets.value = await getJSON('/api/datasets')
  batches.value = await getJSON('/api/batches')
  if (!datasetId.value && datasets.value.length) datasetId.value = datasets.value[0].id
  await loadGate()
}
async function saveConfig() {
  err.value = ''; msg.value = ''
  try {
    const r = await fetch(`/api/profile-gate/${datasetId.value}/config`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        gate_enabled: !!config.value.gate_enabled,
        update_policy: config.value.update_policy,
      }),
    })
    if (!r.ok) throw new Error(await r.text())
    config.value = await r.json()
    msg.value = '门禁配置已保存'
  } catch (e) { err.value = String(e.message || e) }
}
async function saveBaseline(b) {
  err.value = ''; msg.value = ''
  try {
    await patchJSON(`/api/profile-gate/baselines/${b.id}`, {
      baseline_value: Number(b.baseline_value),
      threshold: Number(b.threshold),
      threshold_mode: b.threshold_mode,
      enabled: !!b.enabled,
    })
    msg.value = `基线 #${b.id} 已保存`
    await loadGate()
  } catch (e) { err.value = String(e.message || e) }
}
async function removeBaseline(b) {
  err.value = ''; msg.value = ''
  try {
    const r = await fetch(`/api/profile-gate/baselines/${b.id}`, { method: 'DELETE' })
    if (!r.ok) throw new Error(await r.text())
    msg.value = `基线 #${b.id} 已删除`
    await loadGate()
  } catch (e) { err.value = String(e.message || e) }
}
async function addBaseline() {
  err.value = ''; msg.value = ''
  try {
    await postJSON(`/api/profile-gate/${datasetId.value}/baselines`, {
      field: newEntry.value.field,
      metric: newEntry.value.metric,
      baseline_value: Number(newEntry.value.baseline_value),
      threshold: Number(newEntry.value.threshold),
      threshold_mode: newEntry.value.threshold_mode,
      enabled: !!newEntry.value.enabled,
    })
    msg.value = '基线已登记（同字段同指标会覆盖原条目）'
    newEntry.value.field = ''
    await loadGate()
  } catch (e) { err.value = String(e.message || e) }
}
onMounted(async () => {
  try { await loadAll() } catch (e) { err.value = String(e.message || e) }
})
</script>

<template>
  <div class="page">
    <header>
      <h1>画像基线门禁</h1>
      <p class="muted">行级规则跑完后比对批次画像与历史基线：任一监控字段超阈 → 整批阻断签发精选</p>
    </header>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <p v-if="msg" class="tag ok">{{ msg }}</p>

    <div class="panel row">
      <label>数据集
        <select v-model="datasetId" @change="loadGate">
          <option v-for="d in datasets" :key="d.id" :value="d.id">{{ d.id }} · {{ d.name }}</option>
        </select>
      </label>
    </div>

    <div class="panel" v-if="config">
      <h3>门禁开关与基线更新策略（互斥）</h3>
      <div class="row">
        <label><input type="checkbox" v-model="config.gate_enabled" :true-value="1" :false-value="0" /> 启用画像门禁</label>
        <label><input type="radio" v-model="config.update_policy" value="frozen" /> 冻结基线（成功签发也不改基线）</label>
        <label><input type="radio" v-model="config.update_policy" value="rolling" /> 滚动更新（仅未阻断且签发成功的批次按本批画像更新）</label>
        <button @click="saveConfig">保存配置</button>
      </div>
      <p class="muted">阻断批次一律禁止更新基线；rolling 下 released=0 的批次也不算「签发成功」。</p>
    </div>

    <div class="panel">
      <h3>画像基线与阈值</h3>
      <table>
        <thead>
          <tr><th>字段</th><th>指标</th><th>基线值</th><th>阈值</th><th>口径</th><th>启用</th><th>更新时间</th><th>来源批次</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="b in baselines" :key="b.id">
            <td><code>{{ b.field }}</code></td>
            <td>{{ METRIC_LABEL[b.metric] || b.metric }}<br /><span class="muted">{{ b.metric }}</span></td>
            <td><input type="number" step="any" v-model="b.baseline_value" style="width:90px" /></td>
            <td><input type="number" step="any" v-model="b.threshold" style="width:80px" /></td>
            <td>
              <select v-model="b.threshold_mode">
                <option value="abs">abs 绝对差</option>
                <option value="rel">rel 相对差</option>
              </select>
            </td>
            <td><input type="checkbox" v-model="b.enabled" :true-value="1" :false-value="0" /></td>
            <td class="muted">{{ b.updated_at }}</td>
            <td class="muted">{{ b.updated_from_batch_id ?? '—' }}</td>
            <td class="row">
              <button @click="saveBaseline(b)">保存</button>
              <button class="btn-danger" @click="removeBaseline(b)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
      <h4>登记新基线</h4>
      <div class="row">
        <input v-model="newEntry.field" placeholder="字段名，如 sku" style="width:140px" />
        <select v-model="newEntry.metric">
          <option value="null_rate">空值率 null_rate</option>
          <option value="distinct_rate">去重率 distinct_rate</option>
          <option value="mean">均值 mean</option>
        </select>
        <label>基线值 <input type="number" step="any" v-model="newEntry.baseline_value" style="width:90px" /></label>
        <label>阈值 <input type="number" step="any" v-model="newEntry.threshold" style="width:80px" /></label>
        <select v-model="newEntry.threshold_mode">
          <option value="abs">abs 绝对差</option>
          <option value="rel">rel 相对差</option>
        </select>
        <button :disabled="!newEntry.field" @click="addBaseline">登记</button>
      </div>
    </div>

    <div class="panel">
      <h3>口径说明</h3>
      <ul class="muted">
        <li><b>null_rate 空值率</b> = 字段为空的行数 / 本批扫描行数；「空」= 缺失、null、或去空白后为空字符串（0/false 不算空）。</li>
        <li><b>distinct_rate 去重率</b> = 非空取值去重个数 / 非空取值个数；无非空值时记 0。</li>
        <li><b>mean 均值</b> = 非空取值中可解析为有限数字者（布尔除外）的算术平均；无可用数值时跳过比对。</li>
        <li><b>abs 绝对差</b>：|本批 − 基线| &gt; 阈值 → 超阈；<b>rel 相对差</b>：|本批 − 基线| &gt; 阈值 × |基线| → 超阈（基线为 0 时退化为绝对差）。</li>
        <li>画像口径 = 本批全部扫描行（含后续被行级规则隔离的行），与行级规则结果无关。</li>
      </ul>
    </div>

    <div class="panel" v-if="blockedBatches.length">
      <h3>近期画像阻断批次</h3>
      <table>
        <thead><tr><th>批次</th><th>数据集</th><th>时间</th><th>阻断原因</th></tr></thead>
        <tbody>
          <tr v-for="b in blockedBatches" :key="b.id">
            <td>#{{ b.id }}</td>
            <td>{{ b.dataset_id }}</td>
            <td class="muted">{{ b.created_at }}</td>
            <td>
              <span class="tag danger">profile_blocked</span>
              <div v-for="(r, i) in b.report.profile_gate.block_reasons" :key="i" class="muted">{{ r }}</div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
