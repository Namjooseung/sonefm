<script setup>
// 방송일지 화면: 달을 골라 엑셀(하루 1시트)로 만든다. 편성표(계획) 그대로.
import { ref } from 'vue'
import * as api from '../api.js'

const now = new Date()
const year = ref(now.getFullYear())
const month = ref(now.getMonth() + 1)
const years = [year.value - 1, year.value, year.value + 1]
const busy = ref(false)
const result = ref(null)      // { ok, message, path, warnings } | { ok: false, errors }

async function make() {
  busy.value = true
  const r = await api.journalExport(year.value, month.value)
  busy.value = false
  if (!r.cancelled) result.value = r
}
</script>

<template>
  <section class="page">
    <h1>방송일지</h1>
    <div class="card body">
      <p class="muted small">고른 달의 1일부터 말일까지 하루에 시트 한 장씩 엑셀로 만듭니다. 편성표에 있는 그대로 들어갑니다.</p>
      <div class="row">
        <select v-model="year" class="select" aria-label="연도">
          <option v-for="y in years" :key="y" :value="y">{{ y }}년</option>
        </select>
        <select v-model="month" class="select" aria-label="월">
          <option v-for="m in 12" :key="m" :value="m">{{ m }}월</option>
        </select>
        <button class="btn btn-primary" :disabled="busy" @click="make">{{ busy ? '만드는 중…' : '방송일지 만들기' }}</button>
      </div>
    </div>

    <div v-if="result && !result.ok" class="callout callout-danger" role="alert">{{ result.errors[0] }}</div>
    <template v-else-if="result">
      <div class="callout callout-success" role="status">
        {{ result.message }}<br><span class="small">{{ result.path }}</span>
      </div>
      <div v-if="result.warnings.length" class="callout callout-warning">
        <p>확인할 칸 {{ result.warnings.length }}개 — 편성표에서 채우거나 프로그램 분야를 확인한 뒤 다시 만드세요.</p>
        <ul class="warn small"><li v-for="w in result.warnings" :key="w">{{ w }}</li></ul>
      </div>
    </template>
  </section>
</template>

<style scoped>
.page { padding: var(--space-300); display: grid; gap: var(--space-200); align-content: start; max-width: 1100px; overflow: auto; height: 100%; }
h1 { font-size: var(--text-400); line-height: var(--leading-400); font-weight: var(--font-weight-bold); }
.body { padding: var(--space-300); display: grid; gap: var(--space-150); }
.body p, .callout p { margin: 0; }
.row { display: flex; gap: var(--space-100); align-items: center; flex-wrap: wrap; }
.warn { max-height: 320px; overflow: auto; margin-top: var(--space-100); }
</style>
