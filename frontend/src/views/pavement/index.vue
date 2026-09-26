<template>
  <section class="page" data-module="pavement">
    <header class="page-head">
      <div>
        <h2>路面状况管理</h2>
        <p class="page-desc">
          分档由系统按固化规则自动给出（优/良/中/差），评价员不再手填；
          路面损坏指数超过 80 一票按差，抗滑系数低于下限须标明具体偏离。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记路面评价</button>
        <button class="btn" type="button" @click="openRules">分档规则 v{{ currentRuleVersion }}</button>
        <button class="btn" type="button" @click="reevaluateAll">按最新规则重评全部</button>
        <button class="btn" type="button" @click="exportRows">导出清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>评价编号</span>
        <input v-model="filters.keyword" placeholder="按评价编号检索" />
      </label>
      <label class="filter-item">
        <span>道路名称</span>
        <input v-model="filters.road" placeholder="按道路名称检索" />
      </label>
      <label class="filter-item">
        <span>评价路段</span>
        <input v-model="filters.section" placeholder="按评价路段检索" />
      </label>
      <label class="filter-item">
        <span>自动分档</span>
        <select v-model="filters.grade">
          <option value="">全部</option>
          <option v-for="g in grades" :key="g" :value="g">{{ g }}</option>
          <option value="UNGRADED">未分档</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>自动分档</th>
          <th>分档依据 / 未分档原因</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>
            <span v-if="row['分档']" class="grade-badge" :class="gradeClass(String(row['分档']))">{{ row['分档'] }}</span>
            <span v-else class="grade-badge grade-none">未分档</span>
            <div class="rule-tag">规则 v{{ row['规则版本'] ?? '—' }}</div>
          </td>
          <td class="reason-cell">
            <template v-if="row['分档']">
              <button class="link" type="button" @click="openHistory(row)">查看分档依据</button>
            </template>
            <template v-else>
              <span v-for="(reason, idx) in row['未分档原因']" :key="idx" class="error-text reason-line">
                {{ reason }}
              </span>
            </template>
          </td>
          <td class="row-actions">
            <button class="link" type="button" @click="openHistory(row)">依据追溯</button>
            <button class="link" type="button" @click="openEdit(row)">更正指标</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 3" class="empty-state">暂无符合条件的路面状况记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条路面状况记录</span>
      <span v-if="message" :class="messageOk ? '' : 'error-text'">{{ message }}</span>
    </footer>

    <!-- 登记 / 更正指标 -->
    <div v-if="formOpen" class="modal-mask" @click.self="formOpen = false">
      <div class="modal">
        <h3>{{ formMode === 'create' ? '登记路面评价' : '更正指标并重新评定' }}</h3>
        <p class="page-desc">
          评价编号、道路名称、评价路段必填；三项指标缺失或格式不对时允许保存但不分档，并标注原因。
        </p>
        <div class="form-grid">
          <label v-for="field in formMode === 'create' ? createFields : indicatorFields" :key="field.key">
            <span>{{ field.label }}<em v-if="field.required">*</em></span>
            <input v-model="form[field.key]" :placeholder="field.hint ?? field.label" />
          </label>
        </div>
        <div class="modal-actions">
          <button class="btn" type="button" @click="formOpen = false">取消</button>
          <button class="btn primary" type="button" @click="submitForm">保存并自动分档</button>
        </div>
      </div>
    </div>

    <!-- 分档依据追溯 -->
    <div v-if="historyOpen" class="modal-mask" @click.self="historyOpen = false">
      <div class="modal modal-wide">
        <h3>分档依据追溯 · {{ activeRow?.['评价编号'] }}</h3>
        <p class="page-desc">
          判定口径为「道路名称 + 评价路段」。以下为该评价编号下每一次评定的归档快照，
          录入当时那份依据不会因规则改版而变化；指纹用于复核比对。
        </p>
        <div v-for="(snap, idx) in history" :key="idx" class="snapshot">
          <div class="snapshot-head">
            <span v-if="snap.grade" class="grade-badge" :class="gradeClass(snap.grade)">第{{ history.length - idx }}次评定：{{ snap.grade }}</span>
            <span v-else class="grade-badge grade-none">第{{ history.length - idx }}次评定：未分档</span>
            <span class="rule-tag">规则 v{{ snap.rule_version }}</span>
            <span class="snapshot-meta">{{ snap.trigger }} · {{ snap.evaluated_at }}</span>
            <span class="snapshot-meta" :class="snap['依据一致'] ? 'fp-ok' : 'error-text'">
              {{ snap['依据一致'] ? '依据指纹一致' : '依据已被改动！' }} {{ snap.fingerprint }}
            </span>
          </div>
          <p v-if="snap.summary" class="snapshot-summary">{{ snap.summary }}</p>
          <table v-if="snap.indicators?.length" class="data-table inner-table">
            <thead>
              <tr><th>指标</th><th>录入值</th><th>单项档</th><th>命中条款</th><th>附注</th></tr>
            </thead>
            <tbody>
              <tr v-for="(ind, i) in snap.indicators" :key="i">
                <td>{{ ind['指标'] }}</td>
                <td>{{ ind['录入值'] }}</td>
                <td>{{ ind['单项档'] }}</td>
                <td>{{ ind['命中条款'] }}</td>
                <td>{{ ind['附注'] ?? '—' }}</td>
              </tr>
            </tbody>
          </table>
          <ul v-if="snap.reasons?.length" class="reason-list">
            <li v-for="(reason, i) in snap.reasons" :key="i" class="error-text">{{ reason }}</li>
          </ul>
        </div>
        <div class="modal-actions">
          <button class="btn primary" type="button" @click="historyOpen = false">关闭</button>
        </div>
      </div>
    </div>

    <!-- 规则版本 -->
    <div v-if="rulesOpen" class="modal-mask" @click.self="rulesOpen = false">
      <div class="modal modal-wide">
        <h3>分档规则版本</h3>
        <p class="page-desc">
          规则只允许发布新版本、不能覆盖旧版本；每条记录固化评定时的版本号。
          调整阈值后请回到列表点「按最新规则重评全部」。
        </p>
        <div v-for="rule in rules" :key="rule.version" class="snapshot">
          <div class="snapshot-head">
            <strong>v{{ rule.version }}</strong>
            <span class="snapshot-meta">{{ rule.published_at }} · {{ rule.published_by }}</span>
          </div>
          <p class="snapshot-summary">{{ rule.note }}</p>
          <div class="rule-blocks">
            <div v-for="(detail, name) in ruleThresholds(rule)" :key="name" class="rule-block">
              <strong>{{ name }}</strong>
              <span v-for="(text, g) in detail" :key="g" class="rule-line" :class="gradeClass(String(g))">
                {{ g }}：{{ text }}
              </span>
            </div>
          </div>
        </div>

        <details class="publish-box">
          <summary>发布新版阈值（发布后需点「按最新规则重评全部」）</summary>
          <div class="form-grid">
            <label v-for="key in ruleKeys" :key="key">
              <span>{{ ruleLabels[key] }}</span>
              <input v-model="ruleForm[key]" type="number" step="0.01" />
            </label>
            <label>
              <span>发布人</span>
              <input v-model="ruleForm.published_by" placeholder="发布人" />
            </label>
            <label class="span-2">
              <span>改动说明</span>
              <input v-model="ruleForm.note" placeholder="如：否决线收紧到 70" />
            </label>
          </div>
          <div class="modal-actions">
            <button class="btn primary" type="button" @click="publishRule">发布新版本</button>
          </div>
        </details>
        <div class="modal-actions">
          <button class="btn primary" type="button" @click="rulesOpen = false">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | string[] | null>
type Snapshot = Record<string, any>

const ENDPOINT = '/api/pavement'
const columns = ['评价编号', '道路名称', '评价路段', '路面损坏指数', '平整度指数', '抗滑系数', '车辙深度', '评价日期']
const grades = ['优', '良', '中', '差']

interface FieldDef { key: string; label: string; required?: boolean; hint?: string }
const createFields: FieldDef[] = [
  { key: '评价编号', label: '评价编号', required: true, hint: '如 PAVE-2026-009' },
  { key: '道路名称', label: '道路名称', required: true },
  { key: '评价路段', label: '评价路段', required: true, hint: '同名道路按路段分别登记' },
  { key: '路面损坏指数', label: '路面损坏指数 PCI', hint: '0~100，超过 80 一票按差' },
  { key: '平整度指数', label: '平整度指数 IRI', hint: 'm/km，如 3.2' },
  { key: '抗滑系数', label: '抗滑系数 SFC', hint: '0~1，低于 0.30 为差' },
  { key: '车辙深度', label: '车辙深度' },
  { key: '评价日期', label: '评价日期', hint: 'YYYY-MM-DD' },
]
const indicatorFields: FieldDef[] = createFields.slice(3)

const rows = ref<Row[]>([])
const total = ref(0)
const message = ref('')
const messageOk = ref(true)
const filters = ref<Record<string, string>>({ keyword: '', road: '', section: '', grade: '' })

const formOpen = ref(false)
const formMode = ref<'create' | 'edit'>('create')
const editingId = ref<number | null>(null)
const form = reactive<Record<string, string>>({})

const historyOpen = ref(false)
const activeRow = ref<Row | null>(null)
const history = ref<Snapshot[]>([])

const rulesOpen = ref(false)
const rules = ref<Snapshot[]>([])
const currentRuleVersion = ref(1)

const ruleKeys = [
  'pci_excellent', 'pci_good', 'pci_fair', 'pci_poor_veto',
  'iri_excellent', 'iri_good', 'iri_fair',
  'sfc_excellent', 'sfc_good', 'sfc_fair',
] as const
const ruleLabels: Record<string, string> = {
  pci_excellent: 'PCI 优（≤）',
  pci_good: 'PCI 良（≤）',
  pci_fair: 'PCI 中（≤）',
  pci_poor_veto: 'PCI 一票否决线（>）',
  iri_excellent: 'IRI 优（≤）',
  iri_good: 'IRI 良（≤）',
  iri_fair: 'IRI 中（≤）',
  sfc_excellent: 'SFC 优（≥）',
  sfc_good: 'SFC 良（≥）',
  sfc_fair: 'SFC 差下限（<）',
}
const ruleForm = reactive<Record<string, string>>({})

const stats = computed(() => {
  const count = (g: string | null) => rows.value.filter(r => (g === null ? !r['分档'] : r['分档'] === g)).length
  return [
    { label: '优级路段', value: count('优') },
    { label: '良级路段', value: count('良') },
    { label: '中级路段', value: count('中') },
    { label: '差级路段', value: count('差') },
    { label: '未分档（指标缺失/格式不对）', value: count(null) },
  ]
})

function gradeClass(grade: string): string {
  return { 优: 'grade-a', 良: 'grade-b', 中: 'grade-c', 差: 'grade-d' }[grade] ?? 'grade-none'
}

function ruleThresholds(rule: Snapshot): Snapshot {
  return {
    路面损坏指数: rule['路面损坏指数'],
    平整度指数: rule['平整度指数'],
    抗滑系数: rule['抗滑系数'],
  }
}

function flash(text: string, ok = true) {
  message.value = text
  messageOk.value = ok
}

function resetFilters() {
  filters.value = { keyword: '', road: '', section: '', grade: '' }
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  formMode.value = 'create'
  editingId.value = null
  Object.keys(form).forEach(k => delete form[k])
  createFields.forEach(f => { form[f.key] = '' })
  formOpen.value = true
}

function openEdit(row: Row) {
  formMode.value = 'edit'
  editingId.value = Number(row.id)
  Object.keys(form).forEach(k => delete form[k])
  indicatorFields.forEach(f => { form[f.key] = String(row[f.key] ?? '') })
  formOpen.value = true
}

async function submitForm() {
  const values: Record<string, string> = {}
  Object.entries(form).forEach(([k, v]) => { values[k] = String(v ?? '').trim() })
  try {
    const url = formMode.value === 'create'
      ? ENDPOINT
      : `${ENDPOINT}/${editingId.value}/indicators`
    const response = await request(url, { method: 'POST', body: JSON.stringify({ values }) })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message)
    flash(payload.message, true)
    formOpen.value = false
    await reload()
  } catch (error) {
    flash(error instanceof Error ? error.message : '保存失败', false)
  }
}

async function openHistory(row: Row) {
  activeRow.value = row
  historyOpen.value = true
  try {
    const response = await request(`${ENDPOINT}/${row.id}/grading-history`)
    const payload = await response.json()
    history.value = [...(payload.history ?? [])].reverse()
  } catch (error) {
    history.value = []
    flash(error instanceof Error ? error.message : '分档依据读取失败', false)
  }
}

async function openRules() {
  rulesOpen.value = true
  try {
    const response = await request(`${ENDPOINT}/rules`)
    const payload = await response.json()
    rules.value = payload.rules ?? []
    currentRuleVersion.value = payload.current_version ?? 1
    prefillRuleForm(rules.value[0])
  } catch (error) {
    flash(error instanceof Error ? error.message : '规则读取失败', false)
  }
}

function lastNumber(text: string): string {
  const match = String(text).match(/-?\d+(\.\d+)?/g)
  return match ? match[match.length - 1] : ''
}

function prefillRuleForm(rule: Snapshot | undefined) {
  if (!rule) return
  ruleKeys.forEach(k => { ruleForm[k] = '' })
  ruleForm.published_by = ''
  ruleForm.note = ''
  const pci = rule['路面损坏指数'] ?? {}
  const iri = rule['平整度指数'] ?? {}
  const sfc = rule['抗滑系数'] ?? {}
  ruleForm.pci_excellent = lastNumber(pci['优'])
  ruleForm.pci_good = lastNumber(pci['良'])
  ruleForm.pci_fair = lastNumber(pci['中'])
  ruleForm.pci_poor_veto = lastNumber(pci['差'])
  ruleForm.iri_excellent = lastNumber(iri['优'])
  ruleForm.iri_good = lastNumber(iri['良'])
  ruleForm.iri_fair = lastNumber(iri['中'])
  ruleForm.sfc_excellent = lastNumber(sfc['优'])
  ruleForm.sfc_good = lastNumber(sfc['良'])
  ruleForm.sfc_fair = lastNumber(sfc['差'])
}

async function publishRule() {
  const values: Record<string, string> = {}
  ruleKeys.forEach(k => { if (ruleForm[k] !== '') values[k] = ruleForm[k] })
  values.published_by = ruleForm.published_by
  values.note = ruleForm.note
  try {
    const response = await request(`${ENDPOINT}/rules/publish`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message)
    flash(payload.message, true)
    currentRuleVersion.value = payload.entry?.version ?? currentRuleVersion.value
    await openRules()
  } catch (error) {
    flash(error instanceof Error ? error.message : '规则发布失败', false)
  }
}

async function reevaluateAll() {
  flash('正在按最新规则重评全部记录…', true)
  try {
    const response = await request(`${ENDPOINT}/reevaluate`, {
      method: 'POST',
      body: JSON.stringify({ values: {} }),
    })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message)
    flash(payload.message, true)
    await reload()
  } catch (error) {
    flash(error instanceof Error ? error.message : '重评失败', false)
  }
}

async function reload() {
  message.value = ''
  const params = new URLSearchParams()
  Object.entries(filters.value).forEach(([k, v]) => {
    if (!v) return
    if (k === 'grade') {
      if (v === 'UNGRADED') params.set('ungraded_only', 'true')
      else params.set('grade', v)
    } else {
      params.set(k, v)
    }
  })
  try {
    const response = await request(`${ENDPOINT}?${params.toString()}`)
    if (!response.ok) throw new Error('路面评价列表读取失败')
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    flash(error instanceof Error ? error.message : '路面评价列表读取失败', false)
  }
}

onMounted(() => {
  void reload()
  void request(`${ENDPOINT}/grades`)
    .then(r => r.json())
    .then(p => { currentRuleVersion.value = p.current_rule?.version ?? 1 })
    .catch(() => undefined)
})
</script>

<style scoped>
.page-actions { display: flex; gap: 8px; }
.grade-badge {
  display: inline-block; min-width: 34px; text-align: center;
  padding: 2px 8px; border-radius: 10px; font-size: 12px; font-weight: 600;
}
.grade-a { background: #dcfae6; color: #067647; }
.grade-b { background: #e0edff; color: #1f6feb; }
.grade-c { background: #fef0c7; color: #b54708; }
.grade-d { background: #fee4e2; color: #b42318; }
.grade-none { background: #eef1f5; color: #64748b; }
.rule-tag { display: block; margin-top:4px; font-size: 11px; color: #94a3b8; }
.reason-cell { max-width: 260px; }
.reason-line { display: block; font-size: 12px; line-height: 1.5; }
.modal-mask {
  position: fixed; inset: 0; background: rgba(15, 23, 42, 0.45);
  display: flex; align-items: flex-start; justify-content: center; padding: 40px 16px;
  z-index: 50; overflow-y: auto;
}
.modal {
  background: #fff; border-radius: 10px; padding: 20px 24px; width: 560px;
  box-shadow: 0 12px 40px rgba(15, 23, 42, 0.25);
}
.modal-wide { width: 820px; }
.modal h3 { margin: 0 0 6px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 14px; margin: 12px 0; }
.form-grid label span, .form-grid em { font-size: 12px; color: #64748b; font-style: normal; }
.form-grid em { color: #b42318; margin-left: 2px; }
.form-grid input { width: 100%; padding: 6px 8px; border: 1px solid #d8dee6; border-radius: 6px; margin-top: 2px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 8px; }
.snapshot { border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px 12px; margin-top: 10px; }
.snapshot-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.snapshot-meta { font-size: 12px; color: #64748b; }
.fp-ok { color: #067647; }
.snapshot-summary { margin: 8px 0; font-size: 13px; }
.inner-table { margin-top: 6px; }
.reason-list { margin: 6px 0 0; padding-left: 18px; }
.rule-blocks { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; }
.rule-block { display: flex; flex-direction: column; gap: 2px; border: 1px solid #eef1f5; border-radius: 6px; padding: 8px; }
.rule-line { font-size: 12px; line-height: 1.6; }
.publish-box { margin-top: 12px; border: 1px dashed #cbd5e1; border-radius: 8px; padding: 8px 12px; font-size: 13px; }
.publish-box summary { cursor: pointer; color: #1f6feb; }
.span-2 { grid-column: 1 / -1; }
</style>
