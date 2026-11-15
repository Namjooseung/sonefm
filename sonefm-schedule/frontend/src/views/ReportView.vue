<script setup>
// 보고서 화면: 달을 골라 방송실시결과 보고서(hwpx, 한글이 있으면 hwp도)를 만든다. 편성표 그대로.
import { ref } from 'vue'
import * as api from '../api.js'

const now = new Date()
const year = ref(now.getFullYear())
const month = ref(now.getMonth() + 1)
const years = [year.value - 1, year.value, year.value + 1]
const busy = ref(false)
const result = ref(null)

async function make() {
  busy.value = true
  const r = await api.reportExport(year.value, month.value)
  busy.value = false
  if (!r.cancelled) result.value = r
}
</script>

<template>
  <section class="page">
    <h1>방송실시결과 보고서</h1>
    <div class="card body">
      <p class="muted small">고른 달의 편성표로 보고서(별지 제13호서식)를 만듭니다. 제목의 월, 전체방송·대중음악·청취자참여·다른 방송사업자 제작 시간, 4면 대중음악 표, 5면 청취자참여 표를 채웁니다. 이 PC에 한글이 있으면 같은 이름의 hwp 파일도 함께 저장합니다.</p>
      <div class="row">
        <select v-model="year" class="select" aria-label="연도">
          <option v-for="y in years" :key="y" :value="y">{{ y }}년</option>
        </select>
        <select v-model="month" class="select" aria-label="월">
          <option v-for="m in 12" :key="m" :value="m">{{ m }}월</option>
        </select>
        <button class="btn btn-primary" :disabled="busy" @click="make">{{ busy ? '만드는 중…' : '보고서 만들기' }}</button>
      </div>
    </div>

    <div v-if="result && !result.ok" class="callout callout-danger" role="alert">{{ result.errors[0] }}</div>
    <template v-else-if="result">
      <div class="callout callout-success" role="status">
        {{ result.message }}<br>
        <span class="small">{{ result.path }}</span>
        <template v-if="result.hwp_path"><br><span class="small">{{ result.hwp_path }}</span></template>
      </div>
      <div class="card body">
        <dl>
          <dt>전체방송</dt><dd>{{ result.summary.total }}분</dd>
          <dt>대중음악</dt><dd>{{ result.summary.music }}분</dd>
          <dt>청취자참여</dt><dd>{{ result.summary.listener }}분</dd>
          <dt>다른 방송사업자 제작</dt><dd>{{ result.summary.external }}분 ({{ result.summary.external_pct }})</dd>
        </dl>
      </div>
      <div v-if="result.warnings.length" class="callout callout-warning">
        <p>확인할 것 {{ result.warnings.length }}개 — 편성표에서 고친 뒤 다시 만드세요.</p>
        <ul class="small"><li v-for="w in result.warnings" :key="w">{{ w }}</li></ul>
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
dl { display: grid; grid-template-columns: 160px 1fr; gap: var(--space-100) var(--space-200); margin: 0; }
dt { color: var(--color-foreground-hint-100); }
dd { margin: 0; font-weight: var(--font-weight-medium); }
</style>
