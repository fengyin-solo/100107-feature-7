<template>
  <section class="page" data-module="pavement">
    <header class="page-head">
      <div>
        <h2>路面状况管理</h2>
        <p class="page-desc">
          分档由固化规则按路面损坏指数、平整度指数、抗滑系数自动比对给出（优 / 良 / 中 / 差）；
          损坏指数超过中上限统一按差，抗滑低于下限给出偏离范围，指标缺失或格式异常只登记不出档。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记路面评价</button>
        <button class="btn" type="button" @click="openRules">分档规则</button>
        <button class="btn" type="button" @click="regradeAll">按当前规则重评全部</button>
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
        <span>评价编号 / 道路名称 / 评价路段</span>
        <input v-model="filters.keyword" placeholder="可按同名道路下不同路段检索" />
      </label>
      <label class="filter-item">
        <span>分档结论</span>
        <select v-model="filters.status">
          <option value="">全部</option>
          <option v-for="g in gradeOptions" :key="g" :value="g">{{ g }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>分档结论</th>
          <th>处置状态</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>
            <span v-if="row['分档结论']" class="grade-badge" :class="badgeClass(row['分档结论'])">{{ row['分档结论'] }}</span>
            <span v-else class="grade-pending" :title="row['不出档原因']">待评定</span>
            <div v-if="row['分档依据']?.['偏离说明']" class="cell-warn">{{ row['分档依据']['偏离说明'] }}</div>
            <div v-if="!row['分档结论']" class="cell-warn">{{ row['不出档原因'] }}</div>
          </td>
          <td>{{ row['处置状态'] ?? '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="showBasis(row)">分档依据</button>
            <button class="link" type="button" @click="regradeRow(row)">重新评价</button>
            <button class="link" type="button" @click="dispatch(row, '下达维修')">下达维修</button>
            <button class="link" type="button" @click="dispatch(row, '记录处置')">记录处置</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 3" class="empty-state">暂无路面状况数据，可先登记路面评价</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条路面状况记录；当前生效规则：{{ activeVersion }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 登记路面评价 -->
    <div v-if="createOpen" class="modal-mask" @click.self="createOpen = false">
      <div class="modal">
        <h3>登记路面评价</h3>
        <p class="page-desc">评价编号、道路名称、评价路段为必填；三项指标缺项或格式不对时可以先登记，但不会生成分档。</p>
        <label v-for="f in createFields" :key="f.key" class="form-item">
          <span>{{ f.label }}<em v-if="f.required">*</em></span>
          <input v-model="createForm[f.key]" :placeholder="f.hint" />
        </label>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="createOpen = false">取消</button>
          <button class="btn primary" type="button" @click="submitCreate">登记并自动分档</button>
        </div>
      </div>
    </div>

    <!-- 分档依据 / 复核 -->
    <div v-if="basisEntry" class="modal-mask" @click.self="basisEntry = null">
      <div class="modal wide">
        <h3>分档依据 · {{ basisEntry['评价编号'] }}</h3>
        <p class="page-desc">
          {{ basisEntry['道路名称'] }} ｜ {{ basisEntry['评价路段'] }}
        </p>
        <template v-if="basisEntry['分档依据']">
          <div class="basis-head">
            <span class="grade-badge" :class="badgeClass(basisEntry['分档结论'])">结论：{{ basisEntry['分档结论'] }}</span>
            <span>规则版本：{{ basisEntry['分档依据']['规则版本'] }}（{{ basisEntry['分档依据']['生效日期'] }} 生效）</span>
            <span>评定时间：{{ basisEntry['分档依据']['评定时间'] }}</span>
          </div>
          <ul class="basis-list">
            <li v-for="(line, i) in basisEntry['分档依据']['依据明细']" :key="i">{{ line }}</li>
          </ul>
          <dl class="threshold-grid">
            <template v-for="name in thresholdNames" :key="name">
              <dt>{{ name }}</dt>
              <dd>{{ formatThreshold(basisEntry['分档依据']['规则阈值'][name]) }}</dd>
            </template>
          </dl>
          <p class="basis-note">{{ basisEntry['分档依据']['规则阈值']['硬性口径'] }}</p>
          <div class="verify-box">
            <button class="btn" type="button" @click="verifyBasis">复核：指纹比对</button>
            <span v-if="verifyResult" :class="verifyResult['指纹一致'] ? 'verify-ok' : 'error-text'">
              {{ verifyResult['指纹一致']
                ? `指纹一致（${verifyResult['快照指纹'].slice(0, 12)}…），与录入时那份依据逐字相同`
                : '指纹不一致，依据内容已被改动，请以分档历史中的留档为准' }}
            </span>
          </div>
          <div v-if="basisEntry['分档历史']?.length > 1" class="history-box">
            <h4>历史留档（规则改动后重评的依据均不可变保留）</h4>
            <ul>
              <li v-for="(snap, i) in basisEntry['分档历史']" :key="i">
                <span class="grade-badge" :class="badgeClass(snap['分档结论'])">{{ snap['分档结论'] }}</span>
                {{ snap['规则版本'] }} ｜ {{ snap['评定时间'] }} ｜ 指纹 {{ snap['快照指纹'].slice(0, 10) }}…
              </li>
            </ul>
          </div>
        </template>
        <template v-else>
          <p class="error-text">当前未生成分档：{{ basisEntry['不出档原因'] }}</p>
          <p class="page-desc">补齐或修正指标后，可在列表中点「重新评价」，依据将按当前生效规则生成。</p>
        </template>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="basisEntry = null">关闭</button>
        </div>
      </div>
    </div>

    <!-- 规则版本 -->
    <div v-if="rulesOpen" class="modal-mask" @click.self="rulesOpen = false">
      <div class="modal wide">
        <h3>分档规则版本</h3>
        <p class="page-desc">规则只增不改；新版本生效后，既有路面状况记录会按新版本重新评一遍，旧依据留档可追溯。</p>
        <table class="data-table">
          <thead>
            <tr>
              <th>版本</th><th>生效日期</th><th>损坏指数(优/良/中上限)</th>
              <th>平整度(优/良/中上限)</th><th>抗滑(优/良/中下限)</th><th>状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="rule in rules" :key="rule['规则版本']">
              <td>{{ rule['规则版本'] }}</td>
              <td>{{ rule['生效日期'] }}</td>
              <td>{{ rule['损坏指数优上限'] }} / {{ rule['损坏指数良上限'] }} / {{ rule['损坏指数中上限'] }}</td>
              <td>{{ rule['平整度优上限'] }} / {{ rule['平整度良上限'] }} / {{ rule['平整度中上限'] }}</td>
              <td>{{ rule['抗滑优下限'] }} / {{ rule['抗滑良下限'] }} / {{ rule['抗滑中下限'] }}</td>
              <td>
                <span v-if="rule['是否生效']" class="verify-ok">生效中</span>
                <span v-else class="grade-pending">历史版本</span>
              </td>
            </tr>
          </tbody>
        </table>
        <h4>发布新版本（发布即生效并重评全部既有记录）</h4>
        <div class="rule-form">
          <label class="form-item"><span>版本号<em>*</em></span><input v-model="ruleForm.version" placeholder="如 v2027.1" /></label>
          <label class="form-item"><span>生效日期</span><input v-model="ruleForm.effective_from" placeholder="如 2027-01-01" /></label>
          <template v-for="g in thresholdInputs" :key="g.key">
            <label class="form-item" v-for="inp in g.inputs" :key="inp.key">
              <span>{{ inp.label }}</span>
              <input v-model="ruleForm[inp.key]" :placeholder="inp.hint" />
            </label>
          </template>
        </div>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="rulesOpen = false">关闭</button>
          <button class="btn primary" type="button" @click="releaseRule">发布并重评全部记录</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>
type Snapshot = Record<string, any>

const ENDPOINT = '/api/pavement'
const columns = ['评价编号', '道路名称', '评价路段', '路面损坏指数', '平整度指数', '车辙深度', '抗滑系数', '评价日期']
const gradeOptions = ['优', '良', '中', '差', '待评定']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = reactive<{ keyword: string; status: string }>({ keyword: '', status: '' })
const activeVersion = ref('')

const stats = computed(() => {
  const count = (g: string) => rows.value.filter((r) => r['分档结论'] === g).length
  return [
    { label: '优级路段', value: count('优') },
    { label: '良级路段', value: count('良') },
    { label: '中级路段', value: count('中') },
    { label: '差 / 待评定路段', value: count('差') + rows.value.filter((r) => !r['分档结论']).length },
  ]
})

function badgeClass(grade: string): string {
  return { 优: 'g-you', 良: 'g-liang', 中: 'g-zhong', 差: 'g-cha' }[grade] ?? ''
}

function resetFilters() {
  filters.keyword = ''
  filters.status = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (filters.keyword) query.set('keyword', filters.keyword)
  if (filters.status) query.set('status', filters.status)
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) throw new Error('路面评价列表读取失败')
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '路面状况列表读取失败'
  }
}

async function loadRulesMeta() {
  try {
    const response = await request(`${ENDPOINT}/rules`)
    if (!response.ok) return
    const payload = await response.json()
    activeVersion.value = payload.active_version
  } catch {
    /* 规则版本读取失败不阻塞列表 */
  }
}

async function dispatch(row: Row, action: string) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

async function regradeRow(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/regrade`, { method: 'POST' })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message)
    errorMessage.value = ''
    await reload()
    const fresh = rows.value.find((r) => r.id === row.id)
    if (fresh) showBasis(fresh)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '重新评价失败'
  }
}

async function regradeAll() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/regrade`, {
      method: 'POST',
      body: JSON.stringify({ values: {} }),
    })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message)
    const r = payload.entry
    errorMessage.value = ''
    window.alert(
      `重评完成（规则 ${r['规则版本']}）：共 ${r['重评记录数']} 条，新增依据 ${r['新增依据份数']} 份，` +
        `结论变化 ${r['结论变化份数']} 条，指标不满足出档 ${r['指标不满足出档条数']} 条，依据未变跳过 ${r['依据未变跳过条数']} 条`,
    )
    await Promise.all([reload(), loadRulesMeta()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '全量重评失败'
  }
}

// ---- 登记 ----
const createOpen = ref(false)
const createFields = [
  { key: '评价编号', label: '评价编号', required: true, hint: '如 PAVE-2026-008，唯一' },
  { key: '道路名称', label: '道路名称', required: true, hint: '同名道路用评价路段区分' },
  { key: '评价路段', label: '评价路段', required: true, hint: '如 中山路 K4+000-K5+200' },
  { key: '路面损坏指数', label: '路面损坏指数', required: false, hint: '0-100，超过中上限(默认80)直接定差' },
  { key: '平整度指数', label: '平整度指数 IRI', required: false, hint: '0-20，越低越好' },
  { key: '车辙深度', label: '车辙深度(mm)', required: false, hint: '选填，留档不参与分档' },
  { key: '抗滑系数', label: '抗滑系数 SFC', required: false, hint: '0-1，越高越好' },
  { key: '评价日期', label: '评价日期', required: false, hint: '如 2026-09-26' },
]
const emptyCreateForm = () => Object.fromEntries(createFields.map((f) => [f.key, '']))
const createForm = reactive<Record<string, string>>(emptyCreateForm())

function openCreate() {
  Object.assign(createForm, emptyCreateForm())
  createOpen.value = true
}

async function submitCreate() {
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, { method: 'POST', body: JSON.stringify({ values: { ...createForm } }) })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message)
    createOpen.value = false
    await reload()
    window.alert(payload.message)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  }
}

// ---- 依据/复核 ----
const basisEntry = ref<Row | null>(null)
const verifyResult = ref<Snapshot | null>(null)
const thresholdNames = ['路面损坏指数', '平整度指数', '抗滑系数']

function showBasis(row: Row) {
  basisEntry.value = row
  verifyResult.value = null
}

function formatThreshold(cfg: Record<string, any>): string {
  const nums = Object.entries(cfg)
    .filter(([k]) => k !== '方向')
    .map(([k, v]) => `${k} ${v}`)
  return `${cfg['方向']}；${nums.join('，')}`
}

async function verifyBasis() {
  if (!basisEntry.value) return
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${basisEntry.value.id}/verify`)
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message)
    verifyResult.value = payload.entry
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '复核失败'
  }
}

// ---- 规则版本 ----
const rulesOpen = ref(false)
const rules = ref<Row[]>([])
const thresholdInputs = [
  {
    key: 'pci',
    inputs: [
      { key: 'pci_you', label: '损坏指数优上限', hint: '默认 30' },
      { key: 'pci_liang', label: '损坏指数良上限', hint: '默认 50' },
      { key: 'pci_zhong', label: '损坏指数中上限', hint: '默认 80，超过即差' },
    ],
  },
  {
    key: 'iri',
    inputs: [
      { key: 'iri_you', label: '平整度优上限', hint: '默认 3' },
      { key: 'iri_liang', label: '平整度良上限', hint: '默认 5' },
      { key: 'iri_zhong', label: '平整度中上限', hint: '默认 8' },
    ],
  },
  {
    key: 'sfc',
    inputs: [
      { key: 'sfc_you', label: '抗滑优下限', hint: '默认 0.50' },
      { key: 'sfc_liang', label: '抗滑良下限', hint: '默认 0.45' },
      { key: 'sfc_zhong', label: '抗滑中下限', hint: '默认 0.40，低于须报偏离' },
    ],
  },
]
const ruleForm = reactive<Record<string, string>>({
  version: '', effective_from: '',
  pci_you: '', pci_liang: '', pci_zhong: '',
  iri_you: '', iri_liang: '', iri_zhong: '',
  sfc_you: '', sfc_liang: '', sfc_zhong: '',
})

async function openRules() {
  rulesOpen.value = true
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/rules`)
    const payload = await response.json()
    rules.value = payload.rules ?? []
    activeVersion.value = payload.active_version
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '规则版本读取失败'
  }
}

async function releaseRule() {
  errorMessage.value = ''
  const values: Record<string, string> = { ...ruleForm }
  try {
    const response = await request(`${ENDPOINT}/rules/release`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message)
    const r = payload.entry['重评报告']
    await Promise.all([reload(), openRules()])
    window.alert(
      `${payload.message}：新增依据 ${r['新增依据份数']} 份，结论变化 ${r['结论变化份数']} 条，` +
        `指标不满足出档 ${r['指标不满足出档条数']} 条`,
    )
    rulesOpen.value = false
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '规则发布失败'
  }
}

onMounted(() => {
  void reload()
  void loadRulesMeta()
})
</script>
