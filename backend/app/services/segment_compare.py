"""分群对比分析领域服务：对比两个分群在不同维度上的差异。

跨模块数据流: user_cohort -> experiment_group -> segment_compare -> anomaly_detector

异常复核:
- 执行对比触发告警时，把观察窗与两组成员快照版本写进异常记录，
  供异常检测器签收时建立复核快照；
- open_anomaly_review 打开异常复核快照，依据触发时两份样本复算当班差值，
  并区分原告警样本（trigger）与复查补充结果（review）。
"""
from __future__ import annotations

from typing import Any

from app.store import store
from app.services.base import BaseModuleService
from app.services import anomaly_samples as samples


class SegmentCompareService(BaseModuleService):
    MODULE = "segment_compare"
    ENTITY = "对比任务"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["对比名称", "分群A", "分群B", "对比维度", "对比结果摘要"]
    REQUIRED_FIELDS = ["对比名称", "分群A", "分群B"]
    STATUS_ORDER = ["待执行", "执行中", "已完成", "执行失败"]
    ACTION_RULES = {"执行对比": "执行中", "取消对比": "执行失败"}
    NEGATIVE_ACTIONS = []

    def execute_compare(self, entry_id: int, *,
                        window: str | None = None) -> dict[str, Any] | None:
        """执行分群对比分析。

        跨模块数据流: segment_compare -> user_cohort（获取分群数据）
                     segment_compare -> anomaly_detector（异常发现时触发检测）
        """
        task = store.find(self.MODULE, entry_id)
        if task is None:
            return {"error": f"对比任务 {entry_id} 不存在"}

        cohort_a_name = task.get("分群A", "")
        cohort_b_name = task.get("分群B", "")
        compare_dimension = task.get("对比维度", "通用维度")
        window = window or samples.current_observation_window()

        # 查找关联的分群数据
        cohort_a = store.find_by_field("user_cohort", "分群名称", cohort_a_name)
        cohort_b = store.find_by_field("user_cohort", "分群名称", cohort_b_name)

        a_size = int(cohort_a[0].get("群组大小", 0)) if cohort_a else 0
        b_size = int(cohort_b[0].get("群组大小", 0)) if cohort_b else 0

        # 记录两组名单当前成员快照版本，随告警一起留档
        a_id = int(cohort_a[0]["id"]) if cohort_a else None
        b_id = int(cohort_b[0]["id"]) if cohort_b else None
        a_version = store.current_cohort_version(a_id) if a_id is not None else 0
        b_version = store.current_cohort_version(b_id) if b_id is not None else 0

        # 模拟对比计算
        diff_percentage = round(abs(a_size - b_size) / max(1, a_size + b_size) * 100, 2)

        has_anomaly = diff_percentage > 30

        anomaly_id: int | None = None
        if has_anomaly and cohort_a:
            record = store.record_anomaly(
                metric_id=a_id or 0,
                severity="warning" if diff_percentage < 50 else "critical",
                description=(
                    f"分群 {cohort_a_name} 与 {cohort_b_name} 差值 {diff_percentage}%"
                    f"｜观察窗 {window}｜版本 A=v{a_version}/B=v{b_version}"
                ),
                value=diff_percentage,
                threshold=30.0,
            )
            anomaly_id = int(record["id"])
            record["来源任务ID"] = entry_id
            record["观察窗"] = window
            record["分群A"] = cohort_a_name
            record["分群B"] = cohort_b_name
            record["成员版本A"] = a_version
            record["成员版本B"] = b_version

        result = {
            "task_name": task.get("对比名称"),
            "task_id": entry_id,
            "observation_window": window,
            "cohort_a": {"name": cohort_a_name, "size": a_size,
                         "member_snapshot_version": a_version},
            "cohort_b": {"name": cohort_b_name, "size": b_size,
                         "member_snapshot_version": b_version},
            "dimension": compare_dimension,
            "diff_percentage": diff_percentage,
            "anomaly_detected": has_anomaly,
            "anomaly_id": anomaly_id,
            "anomaly_severity": "warning" if diff_percentage < 50 else "critical" if has_anomaly else None,
        }

        task["对比结果摘要"] = f"差值 {diff_percentage}%"
        task["status"] = "已完成"
        task["pending"] = False

        return result

    def open_anomaly_review(self, anomaly_id: int,
                            window: str | None = None) -> dict[str, Any]:
        """打开异常复核快照，依据触发时的两份样本复算当班差值。

        跨模块数据流: segment_compare -> anomaly_detector（读取复核留档）
        返回中明确每份材料是 trigger（原告警样本）还是 review（补充结果）。
        """
        from app.services.anomaly_detector import AnomalyDetectorService
        return AnomalyDetectorService().open_review_snapshot(anomaly_id, window)

    def verify_review_by_monday(self, anomaly_id: int,
                                window: str | None = None) -> dict[str, Any]:
        """按周一采样数据核对复查结论。"""
        from app.services.anomaly_detector import AnomalyDetectorService
        return AnomalyDetectorService().verify_with_monday_sample(anomaly_id, window)

    def get_cross_segment_insights(self, entry_id: int) -> dict[str, Any] | None:
        """获取跨分群洞察，关联异常检测结果。

        跨模块数据流: segment_compare -> anomaly_detector
        """
        task = store.find(self.MODULE, entry_id)
        if task is None:
            return None

        # 查找关联的异常记录
        anomalies = [a for a in store.anomaly_records()
                     if task.get("分群A") in str(a.get("描述", ""))
                     or task.get("分群B") in str(a.get("描述", ""))]

        return {
            "task_name": task.get("对比名称"),
            "result_summary": task.get("对比结果摘要"),
            "related_anomalies": len(anomalies),
            "anomaly_details": [
                {"severity": a.get("严重程度"), "desc": a.get("描述"),
                 "time": a.get("时间"), "anomaly_id": a.get("id"),
                 "observation_window": a.get("观察窗")}
                for a in anomalies
            ],
        }
