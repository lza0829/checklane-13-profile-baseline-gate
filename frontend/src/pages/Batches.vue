<script setup>
import { onMounted, ref } from 'vue'
import { getJSON } from '../api'
const rows = ref([])
const detail = ref(null)
const err = ref('')
async function load() {
  rows.value = await getJSON('/api/batches')
}
async function show(id) {
  detail.value = await getJSON(`/api/batches/${id}`)
}
onMounted(async () => {
  try { await load() }
  catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <header>
      <h1>批次</h1>
      <p class="muted">历史入检批次与报告</p>
    </header>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel">
      <table>
        <thead><tr><th>ID</th><th>数据集</th><th>状态</th><th>扫描</th><th>通过</th><th>隔离</th><th>签发</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id" style="cursor:pointer" @click="show(r.id)">
            <td>{{ r.id }}</td>
            <td>{{ r.dataset_id }}</td>
            <td><span class="tag" :class="r.status === 'released' ? 'ok' : (r.status === 'profile_blocked' ? 'danger' : '')">{{ r.status }}</span></td>
            <td>{{ r.scanned_count }}</td>
            <td>{{ r.passed_count }}</td>
            <td>{{ r.quarantined_count }}</td>
            <td>{{ r.released_count }}</td>
            <td>{{ r.created_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="panel" v-if="detail">
      <h3>批次 #{{ detail.id }} 明细</h3>
      <div v-if="detail.report.profile_gate && detail.report.profile_gate.blocked">
        <p class="tag danger">画像阻断：{{ detail.report.profile_gate.block_reasons.join('；') }}</p>
      </div>
      <pre class="muted" style="white-space:pre-wrap">{{ JSON.stringify(detail.report, null, 2) }}</pre>
    </div>
  </div>
</template>
