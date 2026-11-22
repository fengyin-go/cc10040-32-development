<template>
  <section class="review-panel" data-testid="anomaly-review-panel">
    <header class="review-head">
      <h3>{{ title }}</h3>
      <p class="review-tip">
        签收告警时留档触发样本与两组成员版本；复查材料只追加、不覆盖触发记录。
      </p>
    </header>

    <div class="review-controls">
      <label class="review-field">
        <span>告警ID</span>
        <input v-model="anomalyId" data-testid="review-anomaly-id" type="number" min="1" placeholder="如 1" />
      </label>
      <label class="review-field">
        <span>观察窗（周一日期）</span>
        <input v-model="window" data-testid="review-window" type="date" />
      </label>
      <label class="review-field">
        <span>操作人</span>
        <input v-model="operator" data-testid="review-operator" type="text" placeholder="值班员" />
      </label>
      <div class="review-buttons">
        <button class="btn primary" data-testid="review-ack" type="button" @click="onAcknowledge">
          签收并留档
        </button>
        <button class="btn" data-testid="review-open" type="button" @click="onOpen">
          打开复核快照
        </button>
        <button class="btn" data-testid="review-monday" type="button" @click="onMondayCheck">
          周一采样核对
        </button>
      </div>
    </div>

    <p v-if="errorMessage" class="error-text" data-testid="review-error">{{ errorMessage }}</p>
    <p v-if="infoMessage" class="info-text" data-testid="review-info">{{ infoMessage }}</p>

    <div v-if="snapshot && !snapshot.error" class="snapshot-card" data-testid="review-snapshot-card">
      <header class="snapshot-meta">
        <strong>{{ snapshot.snapshot_id }}</strong>
        <span>观察窗 {{ snapshot.observation_window }}</span>
        <span>签收人 {{ snapshot.acknowledged_by }}</span>
        <span class="tag trigger-tag">原告警样本 trigger</span>
      </header>

      <table class="data-table">
        <thead>
          <tr>
            <th>分群</th>
            <th>成员快照版本</th>
            <th>样本版本</th>
            <th>窗内采样总量</th>
            <th>周一采样</th>
            <th>材料性质</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>{{ snapshot.trigger_record.cohort_a.cohort_name }}</td>
            <td>v{{ snapshot.trigger_record.cohort_a.member_snapshot_version }}</td>
            <td>{{ snapshot.trigger_record.cohort_a.sample_version }}</td>
            <td>{{ snapshot.trigger_record.cohort_a.total_sampled }}</td>
            <td>{{ snapshot.trigger_record.cohort_a.monday_sampled }}</td>
            <td><span class="tag trigger-tag">trigger</span></td>
          </tr>
          <tr>
            <td>{{ snapshot.trigger_record.cohort_b.cohort_name }}</td>
            <td>v{{ snapshot.trigger_record.cohort_b.member_snapshot_version }}</td>
            <td>{{ snapshot.trigger_record.cohort_b.sample_version }}</td>
            <td>{{ snapshot.trigger_record.cohort_b.total_sampled }}</td>
            <td>{{ snapshot.trigger_record.cohort_b.monday_sampled }}</td>
            <td><span class="tag trigger-tag">trigger</span></td>
          </tr>
        </tbody>
      </table>

      <p class="diff-line">
        当班差值复算：<strong>{{ snapshot.trigger_record.recalc_diff_percentage }}%</strong>
        （签收留档原值 {{ snapshot.trigger_record.original_diff_percentage }}%）
      </p>

      <div v-if="snapshot.review_materials.length" class="material-block">
        <h4>复查补充材料（{{ snapshot.material_count }}）</h4>
        <table class="data-table">
          <thead>
            <tr>
              <th>#</th>
              <th>分群</th>
              <th>本次版本</th>
              <th>该组触发版本</th>
              <th>对组触发版本</th>
              <th>与对组触发样本差值</th>
              <th>材料性质</th>
              <th>操作人</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="material in snapshot.review_materials" :key="material.seq">
              <td>{{ material.seq }}</td>
              <td>{{ material.cohort_name }}</td>
              <td>v{{ material.member_snapshot_version }}</td>
              <td>v{{ material.trigger_version_of_same_side }}</td>
              <td>{{ material.peer_cohort_name }} v{{ material.peer_trigger_version }}</td>
              <td>{{ material.recalc_diff_against_trigger_peer }}%</td>
              <td><span class="tag review-tag">review 补充</span></td>
              <td>{{ material.operator }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="append-block">
        <label class="review-field">
          <span>另取复查材料的分群</span>
          <select v-model="appendCohort" data-testid="review-append-cohort">
            <option value="">请选择分群</option>
            <option :value="snapshot.trigger_record.cohort_a.cohort_name">
              {{ snapshot.trigger_record.cohort_a.cohort_name }}
            </option>
            <option :value="snapshot.trigger_record.cohort_b.cohort_name">
              {{ snapshot.trigger_record.cohort_b.cohort_name }}
            </option>
          </select>
        </label>
        <input v-model="appendNote" data-testid="review-append-note" placeholder="复查备注（可选）" />
        <button class="btn" data-testid="review-append" type="button" @click="onAppendMaterial">
          另取复查材料
        </button>
      </div>
    </div>

    <div v-if="monday && !monday.error" class="monday-card" data-testid="review-monday-card">
      <h4>周一采样核对（{{ monday.monday_date }}，阈值 {{ monday.threshold }}）</h4>
      <table class="data-table">
        <thead>
          <tr>
            <th>判定依据</th>
            <th>分群A 周一</th>
            <th>分群B 周一</th>
            <th>周一差值</th>
            <th>版本</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="check in monday.checks" :key="check.label + (check.material_seq ?? '')">
            <td>
              <span :class="['tag', check.basis === 'trigger' ? 'trigger-tag' : 'review-tag']">
                {{ check.label }}
              </span>
            </td>
            <td>{{ check.monday_a }}</td>
            <td>{{ check.monday_b }}</td>
            <td>{{ check.monday_diff_percentage }}%</td>
            <td>
              <template v-if="check.basis === 'trigger'">
                A v{{ check.cohort_a_version }} / B v{{ check.cohort_b_version }}
              </template>
              <template v-else>
                复查 v{{ check.review_version }} 对 触发 v{{ check.peer_trigger_version }}
              </template>
            </td>
          </tr>
        </tbody>
      </table>
      <p class="diff-line">复查结论：<strong>{{ monday.conclusion }}</strong></p>
    </div>
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'

import {
  acknowledgeAnomaly,
  appendReviewMaterial,
  mondayCheck,
  openReviewSnapshot,
  type MondayCheck,
  type ReviewSnapshot,
} from '@/api/anomaly-review'

const props = defineProps<{ context?: string }>()
const title = props.context === 'segment_compare'
  ? '异常复核快照（分群对比分析）'
  : '异常复核快照'

const anomalyId = ref('')
const window = ref('')
const operator = ref('')
const appendCohort = ref('')
const appendNote = ref('')
const snapshot = ref<ReviewSnapshot | null>(null)
const monday = ref<MondayCheck | null>(null)
const errorMessage = ref('')
const infoMessage = ref('')

function idOrZero(): number {
  return Number.parseInt(anomalyId.value, 10) || 0
}

function resetFeedback() {
  errorMessage.value = ''
  infoMessage.value = ''
}

async function onAcknowledge() {
  resetFeedback()
  if (!idOrZero()) {
    errorMessage.value = '请先填写要签收的告警ID'
    return
  }
  try {
    const result = (await acknowledgeAnomaly(idOrZero(), {
      window: window.value || undefined,
      operator: operator.value || undefined,
    })) as { error?: string; message?: string; original_diff_percentage?: number }
    if (result.error) {
      errorMessage.value = result.error
      return
    }
    infoMessage.value = `${result.message ?? '已签收'}；当班原差值 ${result.original_diff_percentage}%，实验分群已触发新一轮计算`
    await onOpen()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '签收失败'
  }
}

async function onOpen() {
  resetFeedback()
  if (!idOrZero()) {
    errorMessage.value = '请先填写告警ID'
    return
  }
  try {
    snapshot.value = await openReviewSnapshot(idOrZero(), window.value || undefined)
    if (snapshot.value.error) errorMessage.value = snapshot.value.error
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '复核快照读取失败'
  }
}

async function onAppendMaterial() {
  resetFeedback()
  if (!idOrZero() || !appendCohort.value) {
    errorMessage.value = '请先打开快照并选择要另取材料的分群'
    return
  }
  try {
    const result = (await appendReviewMaterial(idOrZero(), appendCohort.value, {
      window: window.value || undefined,
      operator: operator.value || undefined,
      note: appendNote.value || undefined,
    })) as { error?: string; message?: string }
    if (result.error) {
      errorMessage.value = result.error
      return
    }
    infoMessage.value = result.message ?? '复查材料已追加'
    appendNote.value = ''
    await onOpen()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '复查材料追加失败'
  }
}

async function onMondayCheck() {
  resetFeedback()
  if (!idOrZero()) {
    errorMessage.value = '请先填写告警ID'
    return
  }
  try {
    monday.value = await mondayCheck(idOrZero(), window.value || undefined)
    if (monday.value.error) errorMessage.value = monday.value.error
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '周一核对失败'
  }
}
</script>

<style scoped>
.review-panel {
  margin-top: 24px;
  padding: 16px;
  border: 1px solid var(--border-color, #d9dee7);
  border-radius: 10px;
  background: var(--panel-bg, #fbfcfe);
}

.review-head h3 {
  margin: 0 0 4px;
}

.review-tip {
  margin: 0 0 12px;
  color: #667085;
  font-size: 13px;
}

.review-controls {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: flex-end;
}

.review-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
}

.review-field input,
.review-field select,
.append-block input {
  padding: 6px 10px;
  border: 1px solid #c8d0dc;
  border-radius: 6px;
  min-width: 160px;
}

.review-buttons {
  display: flex;
  gap: 8px;
}

.snapshot-card,
.monday-card {
  margin-top: 16px;
  padding: 12px;
  border: 1px solid #e3e8f0;
  border-radius: 8px;
  background: #fff;
}

.snapshot-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  margin-bottom: 10px;
}

.tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
}

.trigger-tag {
  background: #eef2ff;
  color: #3949ab;
}

.review-tag {
  background: #e8f7ee;
  color: #1b7a3d;
}

.diff-line {
  margin: 10px 0 0;
}

.material-block {
  margin-top: 14px;
}

.append-block {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: flex-end;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px dashed #d9dee7;
}

.info-text {
  color: #1b7a3d;
}

.error-text {
  color: #c0392b;
}
</style>
