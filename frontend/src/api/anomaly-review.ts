/**
 * 异常复核快照接口封装：签收留档、复查材料追加、复核快照回看、周一核对。
 * 与后端 app/routers/cross_module.py 的 /api/cross 路由一一对应。
 */
import { request } from './client'

export type ReviewMaterial = {
  seq: number
  material_kind: 'trigger' | 'review'
  side: 'cohort_a' | 'cohort_b'
  cohort_name: string
  member_snapshot_version: number
  trigger_version_of_same_side: number
  peer_cohort_name: string
  peer_trigger_version: number
  recalc_diff_against_trigger_peer?: number
  appended_at?: string
  operator?: string
  note?: string
}

export type ReviewSnapshot = {
  snapshot_id: string
  anomaly_id: number
  observation_window: string
  source_task_name?: string
  acknowledged_by?: string
  acknowledged_at?: string
  trigger_record: {
    material_kind: 'trigger'
    cohort_a: {
      cohort_name: string
      member_snapshot_version: number
      material_kind: string
      sample_version: string
      total_sampled: number
      monday_sampled: number
    }
    cohort_b: {
      cohort_name: string
      member_snapshot_version: number
      material_kind: string
      sample_version: string
      total_sampled: number
      monday_sampled: number
    }
    original_diff_percentage: number
    recalc_diff_percentage: number
  }
  review_materials: ReviewMaterial[]
  material_count: number
  error?: string
}

export type MondayCheck = {
  snapshot_id: string
  anomaly_id: number
  observation_window: string
  monday_date: string
  threshold: number
  checks: Array<{
    basis: 'trigger' | 'review'
    label: string
    cohort_a_version?: number
    cohort_b_version?: number
    material_seq?: number
    side?: string
    review_version?: number
    peer_trigger_version?: number
    monday_a: number
    monday_b: number
    monday_diff_percentage: number
  }>
  conclusion: string
  error?: string
}

async function postJson(path: string): Promise<unknown> {
  const response = await request(path, { method: 'POST' })
  if (!response.ok) throw new Error(`接口返回 ${response.status}`)
  return response.json()
}

async function getJson<T>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) throw new Error(`接口返回 ${response.status}`)
  return (await response.json()) as T
}

function withWindow(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== '') search.set(key, String(value))
  })
  const query = search.toString()
  return query ? `?${query}` : ''
}

/** 签收告警并建立异常复核快照 */
export function acknowledgeAnomaly(
  anomalyId: number,
  options: { window?: string; operator?: string } = {},
): Promise<unknown> {
  return postJson(
    `/api/cross/anomaly/${anomalyId}/acknowledge${withWindow(options)}`,
  )
}

/** 复查时另取材料：归档新成员快照并追加，不覆盖触发记录 */
export function appendReviewMaterial(
  anomalyId: number,
  cohortName: string,
  options: { window?: string; operator?: string; note?: string } = {},
): Promise<unknown> {
  return postJson(
    `/api/cross/anomaly/${anomalyId}/review-material${withWindow({
      cohort_name: cohortName,
      ...options,
    })}`,
  )
}

/** 打开异常复核快照，依据触发时两份样本复算当班差值 */
export function openReviewSnapshot(
  anomalyId: number,
  window?: string,
): Promise<ReviewSnapshot> {
  return getJson<ReviewSnapshot>(
    `/api/cross/anomaly/${anomalyId}/review-snapshot${withWindow({ window })}`,
  )
}

/** 按周一采样数据核对复查结论 */
export function mondayCheck(
  anomalyId: number,
  window?: string,
): Promise<MondayCheck> {
  return getJson<MondayCheck>(
    `/api/cross/anomaly/${anomalyId}/monday-check${withWindow({ window })}`,
  )
}

/** 列出全部异常复核快照 */
export async function listReviewSnapshots(): Promise<
  Array<{
    snapshot_id: string
    anomaly_id: number
    observation_window: string
    source_task_name: string
    acknowledged_by: string
    acknowledged_at: string
    frozen: boolean
    trigger_diff_percentage: number
    review_material_count: number
  }>
> {
  const payload = await getJson<{ items: unknown }>(
    '/api/cross/anomaly/review-snapshots',
  )
  return (payload as { items: never[] }).items
}
