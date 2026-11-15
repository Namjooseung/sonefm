// SoneFM 업무자동화 화면. 빌드 없이 그대로 로드한다 (Python 쪽: hub/api.py).
const $ = (s) => document.querySelector(s)
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
const api = () => window.pywebview.api
const STATUS = { ok: ['완료', 'st-ok'], partial: ['일부', 'st-partial'], error: ['실패', 'st-error'], running: ['실행 중', ''] }
const WEEK = '일월화수목금토'

let state = null
const watching = new Set()

function toast(msg) {
  const t = document.createElement('div')
  t.className = 'toast'
  t.textContent = msg
  document.body.append(t)
  setTimeout(() => t.remove(), 1800)
}

function copyText(text) {
  const c = $('#clip')
  c.value = text
  c.select()
  document.execCommand('copy')
  toast('복사했습니다')
}

async function load(refresh = false) {
  state = refresh ? await api().refresh() : await api().state()
  render()
}

function render() {
  const d = new Date(state.today + 'T00:00:00')
  $('#today').textContent = `${d.getFullYear()}년 ${d.getMonth() + 1}월 ${d.getDate()}일 (${WEEK[d.getDay()]})`

  const banners = []
  if (state.worklog.warning) banners.push(['warning', state.worklog.warning])
  if (state.schedule.warning) banners.push(['warning', state.schedule.warning])
  state.recipe_problems.forEach((p) => banners.push(['danger', p]))
  state.apps.filter((a) => a.error).forEach((a) => banners.push(['danger', `${a.name}: ${a.error}`]))
  $('#banners').innerHTML = banners.map(([k, m]) => `<div class="banner banner-${k}">${esc(m)}</div>`).join('')

  const wl = state.worklog
  $('#src-worklog').textContent = wl.label + (wl.last_date ? ` · 마지막 날짜 ${wl.last_date.slice(5).replace('-', '/')}` : '')
  $('#src-schedule').textContent = state.schedule.label

  const rows = state.tasks.map((t) => {
    let status = '<span class="muted">대기</span>'
    if (t.running) {
      watching.add(t.running.run_id)
      status = `<span>${esc(t.running.message)}</span><div class="bar"><i style="width:${t.running.pct}%"></i></div>`
    } else if (t.mark === '완료') {
      status = '<span class="st-ok">완료</span>'
    } else if (t.last_run) {
      const [label, cls] = STATUS[t.last_run.status] || [t.last_run.status, '']
      status = `<span class="${cls}" title="${esc(t.last_run.message)}">${label}</span>`
    }
    const kind = t.recipe_id ? `<span class="tag tag-recipe">${esc(t.recipe_name)}</span>` : '<span class="tag">수동</span>'
    let action = ''
    if (!t.running) {
      action = t.recipe_id
        ? `<button class="btn btn-sm btn-primary" data-run="${esc(t.key)}" data-recipe="${esc(t.recipe_id)}">${t.last_run ? '다시 실행' : '실행'}</button>`
        : ''
      action += ` <button class="btn btn-sm" data-mark="${esc(t.key)}" data-done="${t.mark === '완료' ? 0 : 1}">${t.mark === '완료' ? '완료 취소' : '완료'}</button>`
      if (t.source === 'manual') action += ` <button class="btn btn-sm btn-ghost" data-del="${esc(t.id)}" aria-label="업무 지우기">지우기</button>`
    }
    const date = t.date ? '' : '<div class="task-date">날짜 없음</div>'
    return `<tr><td><div class="task-text">${esc(t.text)}</div>${date}</td><td>${kind}</td><td>${status}</td><td>${action}</td></tr>`
  })
  $('#tasks').innerHTML = rows.join('')
  $('#tasks-empty').hidden = rows.length > 0

  $('#apps').innerHTML = state.apps.map((a) => `<li><div><b>${esc(a.name)}</b> <span class="muted small">v${esc(a.version)}${a.installed ? '' : ' · 개발'}</span>
    ${a.error ? `<div class="st-error small">${esc(a.error)}</div>` : ''}</div>
    ${a.has_gui && !a.error ? `<button class="btn btn-sm" data-app="${esc(a.id)}">열기</button>` : ''}</li>`).join('')
    || '<li class="muted">찾은 앱이 없습니다. 앱을 설치하거나 설정에서 앱 폴더를 넣으세요.</li>'

  $('#history').innerHTML = state.history.map((h) => {
    const [label, cls] = STATUS[h.status] || [h.status, '']
    return `<li><span class="${cls}">${label}</span> <span class="muted small">${esc((h.started_at || '').slice(5, 16).replace('T', ' '))}</span>
      <div class="small">${esc(h.task_text)}</div><div class="muted small">${esc(h.message || '')}</div></li>`
  }).join('') || '<li class="muted">아직 없습니다.</li>'
}

// ── 실행 결과 ─────────────────────────────────
function showResult(task, result) {
  const box = $('#result')
  const [label, cls] = STATUS[result.status] || [result.status, '']
  const steps = (result.steps || []).map((s, i) => {
    const [l, c] = STATUS[s.result.status] || [s.result.status, '']
    return `<li><b>${i + 1}. ${esc(s.app_name)}</b> <span class="${c}">${l}</span><div class="small muted">${esc(s.result.message)}</div></li>`
  }).join('')
  const skipped = (result.steps || []).flatMap((s) => s.result.skipped || [])
    .map((s) => `<li>${esc(s.item)}: ${esc(s.reason)}</li>`).join('')
  const a = result.actions || {}
  const buttons = [
    ...(a.open || []).map((x) => `<button class="btn btn-sm" data-open="${esc(x.path)}">${esc(x.label)}</button>`),
    ...(a.copy || []).map((x) => `<button class="btn btn-sm" data-copy="${esc(x.text)}">${esc(x.label)}</button>`),
    ...(a.links || []).map((x) => `<button class="btn btn-sm" data-url="${esc(x.url)}">${esc(x.label)}</button>`),
  ].join('')
  box.className = 'panel result'
  box.innerHTML = `<h2><span>실행 결과</span><span class="${cls}">${label}</span></h2>
    <div class="small">${esc(task)}</div>
    <p>${esc(result.message)}</p>
    <ol class="steps">${steps}</ol>
    ${skipped ? `<b class="small">건너뛴 것</b><ul class="skipped small">${skipped}</ul>` : ''}
    <div class="result-actions">${buttons}</div>`
  box.hidden = false
}

let polling = false
async function poll() {
  if (polling) return
  polling = true
  try { await pollOnce() } finally { polling = false }
}

async function pollOnce() {
  for (const id of [...watching]) {
    const r = await api().run_status(id)
    if (r.done) {
      watching.delete(id)
      const t = state.tasks.find((x) => x.key === r.task_key)
      if (r.result) showResult(t ? t.text : '', r.result)
    }
  }
  if (state && (watching.size || state.tasks.some((t) => t.running))) await load()
}

// ── 대화 상자 ─────────────────────────────────
function openDialog(html, onSubmit) {
  const dlg = $('#dlg')
  dlg.innerHTML = `<form method="dialog">${html}<p class="dlg-error" id="dlg-error"></p>
    <div class="dlg-actions"><button class="btn" value="cancel" formnovalidate>닫기</button>
    <button class="btn btn-primary" value="ok" id="dlg-ok">확인</button></div></form>`
  dlg.querySelector('form').addEventListener('submit', async (e) => {
    if (e.submitter && e.submitter.value === 'cancel') return
    e.preventDefault()
    const err = await onSubmit(dlg)
    if (err) $('#dlg-error').textContent = err
    else dlg.close()
  })
  dlg.showModal()
}

function fieldHtml(f) {
  const id = `f-${f.name}`
  const v = esc(f.value)
  let input
  if (f.type === 'file' || f.type === 'folder') {
    input = `<div class="file-row"><input type="text" id="${id}" name="${esc(f.name)}" value="${v}">
      <button type="button" class="btn" data-pick="${id}" data-kind="${f.type}">찾기</button></div>`
  } else if (f.type === 'date') {
    input = `<input type="date" id="${id}" name="${esc(f.name)}" value="${v}">`
  } else if (f.type === 'int') {
    input = `<input type="number" id="${id}" name="${esc(f.name)}" value="${v}">`
  } else if (f.type === 'text') {
    input = `<textarea id="${id}" name="${esc(f.name)}">${v}</textarea>`
  } else {
    input = `<input type="text" id="${id}" name="${esc(f.name)}" value="${v}">`
  }
  return `<div class="field"><label for="${id}">${esc(f.label || f.name)}${f.optional ? ' <span class="muted small">(선택)</span>' : ''}</label>${input}</div>`
}

async function runDialog(key, recipeId) {
  const f = await api().form(key, recipeId)
  if (!f.ok) return toast(f.message)
  const task = state.tasks.find((t) => t.key === key)
  openDialog(`<h2>${esc(f.recipe.name)}</h2><p class="muted small">${esc(f.recipe.summary)}</p>
    <p class="small">업무: ${esc(task ? task.text : '')}</p>${f.fields.map(fieldHtml).join('')}`, async (dlg) => {
    const values = Object.fromEntries(new FormData(dlg.querySelector('form')).entries())
    const r = await api().start(key, recipeId, values)
    if (!r.ok) return r.message
    watching.add(r.run_id)
    await load()
  })
}

function addDialog() {
  openDialog(`<h2>업무 추가</h2><div class="field"><label for="f-text">업무 내용</label>
    <input type="text" id="f-text" name="text" placeholder="예: 유튜브 화 14시 마을살이" autofocus></div>`, async (dlg) => {
    const r = await api().add_task(dlg.querySelector('#f-text').value)
    if (!r.ok) return r.message
    await load()
  })
}

async function settingsDialog() {
  const s = await api().settings()
  const c = s.config
  openDialog(`<h2>설정</h2>
    ${fieldHtml({ name: 'worklog_folder', label: '업무일지 폴더 (드라이브)', type: 'folder', value: c.worklog_folder })}
    ${fieldHtml({ name: 'worklog_url', label: '업무일지 시트 링크 (있으면 폴더보다 먼저)', type: 'string', value: c.worklog_url, optional: true })}
    ${fieldHtml({ name: 'schedule_inbox', label: '편성 폴더 (드라이브, 편성 담당자가 내보낸 .db)', type: 'folder', value: c.schedule_inbox, optional: true })}
    ${fieldHtml({ name: 'stale_days', label: '업무일지가 며칠 비면 경고할지', type: 'int', value: c.stale_days })}
    ${fieldHtml({ name: 'app_dirs', label: '앱 폴더 (개발용, 한 줄에 하나. 설치한 앱은 자동으로 찾습니다)', type: 'text', value: c.app_dirs.join('\n'), optional: true })}
    <p class="muted small">데이터: ${esc(s.data_dir)}<br>사용자 레시피: ${esc(s.recipe_dir)}</p>`, async (dlg) => {
    const v = Object.fromEntries(new FormData(dlg.querySelector('form')).entries())
    const r = await api().save_settings({ ...v, scan_installed: c.scan_installed })
    if (!r.ok) return r.message
    await load(true)
  })
}

// ── 이벤트 ───────────────────────────────────
document.addEventListener('click', async (e) => {
  const b = e.target.closest('button')
  if (!b) return
  const d = b.dataset
  if (d.run) runDialog(d.run, d.recipe)
  else if (d.mark) { await api().mark(d.mark, d.done === '1'); load() }
  else if (d.del) { await api().delete_task(d.del); load() }
  else if (d.app) { const r = await api().open_app(d.app); if (!r.ok) toast(r.message) }
  else if (d.open) { const r = await api().open_path(d.open); if (!r.ok) toast(r.message) }
  else if (d.copy !== undefined) copyText(d.copy)
  else if (d.url) api().open_url(d.url)
  else if (d.pick) {
    const input = document.getElementById(d.pick)
    const p = d.kind === 'folder' ? await api().pick_folder(input.value) : await api().pick_file(input.value)
    if (p) input.value = p
  }
})
$('#btn-refresh').addEventListener('click', () => load(true))
$('#btn-add').addEventListener('click', addDialog)
$('#btn-settings').addEventListener('click', settingsDialog)

window.addEventListener('pywebviewready', async () => {
  await load(true)
  setInterval(poll, 800)
})
