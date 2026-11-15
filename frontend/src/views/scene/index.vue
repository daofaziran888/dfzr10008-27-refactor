<template>
  <section class="page" data-module="scene">
    <header class="page-head">
      <div>
        <h2>场景化配置模板管理</h2>
        <p class="page-desc">维护场景化配置的预设指标、推荐图表与描述信息，为场景化配置提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记场景模板</button>
        <button class="btn" type="button" @click="exportRows">导出场景化配置模板清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openEdit(row)">编辑模板</button>
            <button class="link" type="button" @click="previewSnapshot(row)">实例化预览</button>
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无场景化配置模板数据，可先登记场景模板</td>
        </tr>
      </tbody>
    </table>

    <section v-if="editRow" class="panel">
      <header class="panel-head">
        <strong>编辑模板 {{ editRow['模板编号'] }}</strong>
        <span class="page-desc">保存只影响下一次发布；已发布版本仍按冻结快照实例化。</span>
      </header>
      <form class="filter-bar" @submit.prevent="saveEdit">
        <label v-for="field in editableFields" :key="field" class="filter-item">
          <span>{{ field }}</span>
          <input v-model="editValues[field]" :placeholder="`填写${field}`" />
        </label>
        <button class="btn primary" type="submit">保存编辑</button>
        <button class="btn ghost" type="button" @click="editRow = null">收起</button>
      </form>
    </section>

    <section v-if="snapshotResult" class="panel">
      <header class="panel-head">
        <strong>实例化读取（{{ snapshotResult['模板编号'] }} 第 {{ snapshotResult['版本号'] }} 版）</strong>
        <span class="page-desc">快照生成时刻：{{ snapshotResult['快照生成时刻'] }}</span>
      </header>
      <table class="data-table">
        <thead>
          <tr><th>实例化参数</th><th>取值</th></tr>
        </thead>
        <tbody>
          <tr v-for="(value, key) in snapshotResult['实例化参数']" :key="String(key)">
            <td>{{ key }}</td>
            <td>{{ value }}</td>
          </tr>
        </tbody>
      </table>
      <button class="btn ghost" type="button" @click="snapshotResult = null">收起</button>
    </section>
    <footer class="page-foot">
      <span>共 {{ total }} 条场景化配置模板记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

type InstantiateResult = {
  '模板编号': string
  '版本号': number
  '快照生成时刻': string
  '实例化参数': Record<string, string>
}

const ENDPOINT = '/api/scene'
const columns = ["模板编号", "模板名称", "适用场景", "预设指标", "推荐图表", "默认参数", "模板描述", "模板状态"]
const actions = ["发布模板", "下线模板", "归档模板"]
const statuses = ["草稿", "已发布", "已下线", "已归档"]
const stats = [{"label": "场景模板总数", "value": 0}, {"label": "已发布模板", "value": 0}, {"label": "已下线模板", "value": 0}]
const editableFields = ["模板名称", "适用场景", "预设指标", "推荐图表", "默认参数", "模板描述"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const editRow = ref<Row | null>(null)
const editValues = ref<Record<string, string>>({})
const snapshotResult = ref<InstantiateResult | null>(null)

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '场景模板登记入口尚未接入审批流'
}

function openEdit(row: Row) {
  errorMessage.value = ''
  editRow.value = row
  editValues.value = editableFields.reduce<Record<string, string>>((values, field) => {
    values[field] = String(row[field] ?? '')
    return values
  }, {})
}

async function saveEdit() {
  if (!editRow.value) {
    return
  }
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${editRow.value.id}`, {
      method: 'PUT',
      body: JSON.stringify({ values: editValues.value }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message ?? '场景模板编辑未生效')
    }
    editRow.value = null
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '场景模板编辑失败'
  }
}

async function previewSnapshot(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/instantiate`)
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail ?? '实例化读取失败')
    }
    snapshotResult.value = payload as InstantiateResult
  } catch (error) {
    snapshotResult.value = null
    errorMessage.value = error instanceof Error ? error.message : '实例化读取失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('场景化配置模板动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '场景化配置模板操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('场景模板列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '场景化配置模板列表读取失败'
  }
}

onMounted(reload)
</script>
