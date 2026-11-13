<script setup>
import { onMounted, ref } from 'vue'
import { getJSON, postJSON } from '../api'
const datasets = ref([])
const datasetId = ref(1)
const busy = ref(false)
const err = ref('')
const last = ref(null)
onMounted(async () => {
  datasets.value = await getJSON('/api/datasets')
  if (datasets.value.length) datasetId.value = datasets.value[0].id
})
async function run() {
  busy.value = true
  err.value = ''
  try {
    last.value = await postJSON('/api/inspect', { dataset_id: Number(datasetId.value) })
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <div class="page">
    <header>
      <h1>入检台</h1>
      <p class="muted">装载夹具 → 执行规则 → 隔离失败行 → 放行通过行到精选区</p>
    </header>
    <div class="panel row">
      <label>数据集
        <select v-model="datasetId">
          <option v-for="d in datasets" :key="d.id" :value="d.id">{{ d.id }} · {{ d.name }}</option>
        </select>
      </label>
      <button :disabled="busy" @click="run">{{ busy ? '入检中…' : '开始入检' }}</button>
    </div>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel" v-if="last">
      <h3>批次 #{{ last.id }} <span class="tag" :class="last.status === 'released' ? 'ok' : (last.status === 'profile_blocked' ? 'danger' : '')">{{ last.status }}</span></h3>
      <div class="tiles">
        <div class="tile"><div class="hero-num">{{ last.scanned_count }}</div><div class="muted">扫描</div></div>
        <div class="tile"><div class="hero-num">{{ last.passed_count }}</div><div class="muted">通过</div></div>
        <div class="tile"><div class="hero-num">{{ last.quarantined_count }}</div><div class="muted">隔离</div></div>
        <div class="tile"><div class="hero-num">{{ last.released_count }}</div><div class="muted">签发</div></div>
      </div>
      <template v-if="last.report.profile_gate">
        <h4>画像基线门禁
          <span class="tag" :class="last.report.profile_gate.blocked ? 'danger' : 'ok'">
            {{ last.report.profile_gate.blocked ? '超阈 · 整批阻断' : '未超阈' }}
          </span>
        </h4>
        <p v-if="last.report.profile_gate.blocked" class="tag danger">
          整批不得新写入精选：{{ last.report.profile_gate.block_reasons.join('；') }}
        </p>
        <p class="muted">
          策略 {{ last.report.profile_gate.policy === 'rolling' ? '滚动更新' : '冻结基线' }} ·
          监控 {{ last.report.profile_gate.monitored_count }} 项 ·
          {{ last.report.profile_gate.baseline_update.reason }}
        </p>
        <table v-if="last.report.profile_gate.fields && last.report.profile_gate.fields.length">
          <thead><tr><th>字段</th><th>指标</th><th>本批值</th><th>基线值</th><th>绝对差</th><th>阈值(口径)</th><th>判定</th></tr></thead>
          <tbody>
            <tr v-for="f in last.report.profile_gate.fields" :key="f.field + f.metric">
              <td><code>{{ f.field }}</code></td>
              <td>{{ f.metric }}</td>
              <td>{{ f.batch_value ?? '—' }}</td>
              <td>{{ f.baseline_value }}</td>
              <td>{{ f.diff_abs ?? '—' }}</td>
              <td>{{ f.threshold }} ({{ f.threshold_mode }})</td>
              <td><span class="tag" :class="f.breached ? 'danger' : 'ok'">{{ f.breached ? '超阈' : '正常' }}</span>
                <span class="muted"> {{ f.note }}</span></td>
            </tr>
          </tbody>
        </table>
      </template>
      <h4>对账</h4>
      <table>
        <thead><tr><th>校验</th><th>结果</th><th>说明</th></tr></thead>
        <tbody>
          <tr v-for="c in last.report.checks" :key="c.name">
            <td>{{ c.name }}</td>
            <td><span class="tag" :class="c.ok ? 'ok' : 'danger'">{{ c.ok ? '通过' : '失败' }}</span></td>
            <td class="muted">{{ c.detail }}</td>
          </tr>
        </tbody>
      </table>
      <h4>行清单</h4>
      <table>
        <thead><tr><th>#</th><th>键</th><th>处置</th><th>命中</th></tr></thead>
        <tbody>
          <tr v-for="r in last.rows" :key="r.id">
            <td>{{ r.row_no }}</td>
            <td>{{ r.entity_key }}</td>
            <td><span class="tag" :class="r.disposition === 'pass' ? 'ok' : 'danger'">{{ r.disposition }}</span></td>
            <td class="muted">{{ r.rule_hits.map(h => h.name).join('；') || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
