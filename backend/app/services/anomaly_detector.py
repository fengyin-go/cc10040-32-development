"""异常检测器领域服务：配置和执行异常检测规则，发现数据异常。

跨模块数据流: segment_compare -> anomaly_detector
用户分群的对比分析结果触发异常检测。

复核留档能力:
- 签收告警时建立「异常复核快照」，读取触发时的两组异常样本并绑定成员名单版本；
- 触发资料按告警身份 + 观察窗持久化且冻结，复查材料只追加、不覆盖；
- 同一告警两次复核之间成员快照可能重算，材料上明确区分 trigger / review。
"""
from __future__ import annotations

import time
from typing import Any

from app.store import store
from app.services.base import BaseModuleService
from app.services import anomaly_samples as samples


class AnomalyDetectorService(BaseModuleService):
    MODULE = "anomaly_detector"
    ENTITY = "检测规则"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["规则名称", "目标指标", "检测算法", "异常阈值", "生效时段"]
    REQUIRED_FIELDS = ["规则名称", "目标指标", "检测算法"]
    STATUS_ORDER = ["待启用", "已启用", "已关闭"]
    ACTION_RULES = {"启用检测": "已启用", "关闭检测": "已关闭"}
    NEGATIVE_ACTIONS = []

    def run_detection(self, entry_id: int) -> dict[str, Any] | None:
        """执行异常检测。

        跨模块数据流: anomaly_detector -> metric_definition（检测目标指标）
        """
        rule = store.find(self.MODULE, entry_id)
        if rule is None:
            return {"error": f"检测规则 {entry_id} 不存在"}
        if rule["status"] != "已启用":
            return {"error": f"检测规则 {entry_id} 未启用"}

        metric_name = rule.get("目标指标", "")
        try:
            threshold = float(str(rule.get("异常阈值", "10")).replace(">", "").replace("<", ""))
        except (ValueError, TypeError):
            threshold = 10.0
        algorithm = rule.get("检测算法", "threshold")

        # 查找关联指标
        metrics = store.find_by_field("metric_definition", "指标名称", metric_name)
        if not metrics:
            return {"error": f"未找到指标 {metric_name}"}

        # 模拟检测
        import random
        random.seed(entry_id)
        metric_id = int(metrics[0].get("id", 0))
        sample_value = random.uniform(threshold * 0.5, threshold * 1.5)

        is_anomaly = sample_value > threshold

        if is_anomaly:
            store.record_anomaly(
                metric_id=metric_id,
                severity="critical" if sample_value > threshold * 1.3 else "warning",
                description=f"指标 {metric_name} 检测到异常：{sample_value:.2f} > {threshold}",
                value=sample_value,
                threshold=threshold,
            )

        # 更新查询日志中相关的异常记录
        store.log_query(
            metric_id=metric_id,
            operator="异常检测器",
            sql_summary=f"异常检测: {rule.get('规则名称')}, 算法={algorithm}",
            row_count=1,
            duration_ms=10.0,
            status="正常" if not is_anomaly else "异常终止",
        )

        return {
            "rule_name": rule.get("规则名称"),
            "metric": metric_name,
            "algorithm": algorithm,
            "threshold": threshold,
            "sample_value": round(sample_value, 2),
            "is_anomaly": is_anomaly,
            "severity": "critical" if is_anomaly and sample_value > threshold * 1.3
                        else "warning" if is_anomaly else "normal",
            "detection_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    # ── 异常复核快照 ──

    def _find_anomaly(self, anomaly_id: int) -> dict[str, Any] | None:
        for record in store.anomaly_records():
            if int(record.get("id", 0)) == anomaly_id:
                return record
        return None

    def _resolve_compare_task(self, anomaly: dict[str, Any]) -> dict[str, Any] | None:
        """从异常描述反推触发它的分群对比任务（含两组分群）。"""
        description = str(anomaly.get("描述", ""))
        for task in store.rows("segment_compare"):
            name_a = str(task.get("分群A", ""))
            name_b = str(task.get("分群B", ""))
            if name_a and name_b and name_a in description and name_b in description:
                return task
        return None

    def _cohort_id(self, cohort_name: str) -> int | None:
        rows = store.find_by_field("user_cohort", "分群名称", cohort_name)
        return int(rows[0]["id"]) if rows else None

    def _cohort_size(self, cohort_name: str) -> int:
        rows = store.find_by_field("user_cohort", "分群名称", cohort_name)
        if not rows:
            return 0
        try:
            return int(rows[0].get("群组大小", 0))
        except (ValueError, TypeError):
            return 0

    def _bind_cohort(self, cohort_name: str, window: str,
                     material_kind: str) -> dict[str, Any]:
        """绑定一组名单：取当前成员快照版本，并按该版本生成异常样本。"""
        cohort_id = self._cohort_id(cohort_name)
        version = store.current_cohort_version(cohort_id) if cohort_id is not None else 0
        sample = samples.build_cohort_samples(
            cohort_name=cohort_name,
            window=window,
            group_size=self._cohort_size(cohort_name),
            version=f"v{version}",
            material_kind=material_kind,
        )
        return {
            "cohort_id": cohort_id,
            "cohort_name": cohort_name,
            "member_snapshot_version": version,
            "sample": sample,
        }

    def acknowledge_anomaly(
        self,
        anomaly_id: int,
        *,
        operator: str = "值班员",
        window: str | None = None,
    ) -> dict[str, Any]:
        """签收告警并建立异常复核快照。

        1. 读取触发时两组分群的异常样本；
        2. 绑定两组名单及其当前成员快照版本；
        3. 按告警身份 + 观察窗持久化并冻结触发留档。
        """
        anomaly = self._find_anomaly(anomaly_id)
        if anomaly is None:
            return {"error": f"告警 {anomaly_id} 不存在"}

        window = window or samples.current_observation_window()
        existing = store.get_review_snapshot(anomaly_id, window)
        if existing is not None:
            return {
                "error": "该告警在此观察窗已签收，触发留档不可覆盖",
                "snapshot_id": existing["snapshot_id"],
                "observation_window": window,
            }

        task = self._resolve_compare_task(anomaly)
        if task is None:
            return {"error": f"告警 {anomaly_id} 未关联含两组分群的对比任务，无法建立复核快照"}

        cohort_a_name = str(task.get("分群A", ""))
        cohort_b_name = str(task.get("分群B", ""))
        cohort_a = self._bind_cohort(cohort_a_name, window, "trigger")
        cohort_b = self._bind_cohort(cohort_b_name, window, "trigger")

        original_diff = samples.diff_percentage(
            cohort_a["sample"], cohort_b["sample"])

        snapshot_id = f"RS-{anomaly_id}-{window}"
        snapshot = {
            "snapshot_id": snapshot_id,
            "anomaly_id": anomaly_id,
            "observation_window": window,
            "source_task_id": task.get("id"),
            "source_task_name": task.get("对比名称"),
            "anomaly_description": anomaly.get("描述"),
            "severity": anomaly.get("严重程度"),
            "threshold": anomaly.get("阈值"),
            "acknowledged_by": operator,
            "acknowledged_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "trigger_record": {
                "material_kind": "trigger",
                "cohort_a": cohort_a,
                "cohort_b": cohort_b,
                "original_diff_percentage": original_diff,
            },
            "review_materials": [],
            "frozen": False,
        }
        store.save_review_snapshot(snapshot)

        anomaly["签收状态"] = "已签收"
        anomaly["复核快照"] = snapshot_id

        # 签收有两组数据的告警后，让实验分群完成新一轮计算。
        from app.services.experiment_group import ExperimentGroupService
        new_round = ExperimentGroupService().run_new_round(
            cohort_a_name, cohort_b_name,
            trigger_snapshot_id=snapshot_id, operator=operator)

        return {
            "snapshot_id": snapshot_id,
            "anomaly_id": anomaly_id,
            "observation_window": window,
            "acknowledged_by": operator,
            "cohort_a": {
                "name": cohort_a_name,
                "member_snapshot_version": cohort_a["member_snapshot_version"],
            },
            "cohort_b": {
                "name": cohort_b_name,
                "member_snapshot_version": cohort_b["member_snapshot_version"],
            },
            "original_diff_percentage": original_diff,
            "experiment_new_round": new_round,
            "message": "告警已签收，异常复核快照已留档",
        }

    def append_review_material(
        self,
        anomaly_id: int,
        window: str,
        *,
        cohort_name: str,
        operator: str = "值班员",
        note: str = "",
    ) -> dict[str, Any]:
        """复查时另取材料：为某一组成员归档新版本并追加进复核快照。

        触发留档不变；新材料标记 material_kind=review，并记录另一组当时所用版本，
        让值班员能分清眼前资料是原告警样本还是本次判断的补充结果。
        """
        snapshot = store.get_review_snapshot(anomaly_id, window)
        if snapshot is None:
            return {"error": f"告警 {anomaly_id} 在观察窗 {window} 尚无复核快照，请先签收"}

        trigger = snapshot["trigger_record"]
        cohort_id = self._cohort_id(cohort_name)
        if cohort_id is None:
            return {"error": f"分群 {cohort_name} 不存在"}

        # 有新的成员归档：另取一份复查材料，生成新版本成员快照。
        from app.services.user_cohort import UserCohortService
        recomputed = UserCohortService().recompute_cohort(
            cohort_id, source="review", operator=operator)
        new_version = store.current_cohort_version(cohort_id)

        review_sample = samples.build_cohort_samples(
            cohort_name=cohort_name,
            window=window,
            group_size=self._cohort_size(cohort_name),
            version=f"v{new_version}",
            material_kind="review",
        )

        # 记录另一组在触发留档里所用的成员快照版本，保证可追溯。
        if cohort_name == trigger["cohort_a"]["cohort_name"]:
            peer = trigger["cohort_b"]
            side = "cohort_a"
        elif cohort_name == trigger["cohort_b"]["cohort_name"]:
            peer = trigger["cohort_a"]
            side = "cohort_b"
        else:
            return {"error": f"分群 {cohort_name} 不属于该复核快照的两组名单"}

        material = {
            "material_kind": "review",
            "side": side,
            "cohort_id": cohort_id,
            "cohort_name": cohort_name,
            "member_snapshot_version": new_version,
            "trigger_version_of_same_side": trigger[side]["member_snapshot_version"],
            "peer_cohort_name": peer["cohort_name"],
            "peer_trigger_version": peer["member_snapshot_version"],
            "sample": review_sample,
            "operator": operator,
            "note": note,
        }
        store.append_review_material(anomaly_id, window, material)

        return {
            "snapshot_id": snapshot["snapshot_id"],
            "anomaly_id": anomaly_id,
            "observation_window": window,
            "appended_material": {k: v for k, v in material.items() if k != "sample"},
            "review_sample_size": review_sample["sample_size"],
            "message": "复查材料已追加，触发留档未变更",
        }

    def open_review_snapshot(self, anomaly_id: int, window: str | None = None) -> dict[str, Any]:
        """打开异常复核快照：依据触发时两份样本复算当班差值，并列出材料版本。"""
        window = window or samples.current_observation_window()
        snapshot = store.get_review_snapshot(anomaly_id, window)
        if snapshot is None:
            return {"error": f"告警 {anomaly_id} 在观察窗 {window} 无复核快照"}

        trigger = snapshot["trigger_record"]
        recalc_diff = samples.diff_percentage(
            trigger["cohort_a"]["sample"], trigger["cohort_b"]["sample"])

        materials: list[dict[str, Any]] = []
        for material in snapshot["review_materials"]:
            # 复查材料与另一组的触发样本复算，体现本次判断所用补充结果。
            if material["side"] == "cohort_a":
                other = trigger["cohort_b"]["sample"]
            else:
                other = trigger["cohort_a"]["sample"]
            materials.append({
                "seq": material["seq"],
                "material_kind": material["material_kind"],
                "side": material["side"],
                "cohort_name": material["cohort_name"],
                "member_snapshot_version": material["member_snapshot_version"],
                "trigger_version_of_same_side": material["trigger_version_of_same_side"],
                "peer_cohort_name": material["peer_cohort_name"],
                "peer_trigger_version": material["peer_trigger_version"],
                "recalc_diff_against_trigger_peer": samples.diff_percentage(
                    material["sample"], other),
                "appended_at": material["appended_at"],
                "operator": material["operator"],
                "note": material.get("note", ""),
            })

        return {
            "snapshot_id": snapshot["snapshot_id"],
            "anomaly_id": anomaly_id,
            "observation_window": window,
            "source_task_name": snapshot["source_task_name"],
            "acknowledged_by": snapshot["acknowledged_by"],
            "acknowledged_at": snapshot["acknowledged_at"],
            "trigger_record": {
                "material_kind": "trigger",
                "cohort_a": self._sample_summary(trigger["cohort_a"]),
                "cohort_b": self._sample_summary(trigger["cohort_b"]),
                "original_diff_percentage": trigger["original_diff_percentage"],
                "recalc_diff_percentage": recalc_diff,
            },
            "review_materials": materials,
            "material_count": len(materials),
        }

    @staticmethod
    def _sample_summary(binding: dict[str, Any]) -> dict[str, Any]:
        sample = binding["sample"]
        return {
            "cohort_name": binding["cohort_name"],
            "member_snapshot_version": binding["member_snapshot_version"],
            "material_kind": sample["material_kind"],
            "sample_version": sample["version"],
            "total_sampled": sample["total_sampled"],
            "monday_sampled": sample["monday_sampled"],
        }

    def verify_with_monday_sample(self, anomaly_id: int,
                                  window: str | None = None) -> dict[str, Any]:
        """按周一采样数据核对复查结论。

        分别用触发样本与最新复查材料（若有）在周一单点上复算差值，
        并标注每份材料究竟是原告警样本还是补充结果。
        """
        window = window or samples.current_observation_window()
        snapshot = store.get_review_snapshot(anomaly_id, window)
        if snapshot is None:
            return {"error": f"告警 {anomaly_id} 在观察窗 {window} 无复核快照"}

        trigger = snapshot["trigger_record"]
        trigger_monday_diff = samples.monday_diff(
            trigger["cohort_a"]["sample"], trigger["cohort_b"]["sample"])

        checks: list[dict[str, Any]] = [{
            "basis": "trigger",
            "label": "原告警所用样本",
            "cohort_a_version": trigger["cohort_a"]["member_snapshot_version"],
            "cohort_b_version": trigger["cohort_b"]["member_snapshot_version"],
            "monday_a": trigger["cohort_a"]["sample"]["monday_sampled"],
            "monday_b": trigger["cohort_b"]["sample"]["monday_sampled"],
            "monday_diff_percentage": trigger_monday_diff,
        }]

        for material in snapshot["review_materials"]:
            if material["side"] == "cohort_a":
                peer_sample = trigger["cohort_b"]["sample"]
                monday_a, monday_b = material["sample"]["monday_sampled"], peer_sample["monday_sampled"]
            else:
                peer_sample = trigger["cohort_a"]["sample"]
                monday_a, monday_b = peer_sample["monday_sampled"], material["sample"]["monday_sampled"]
            checks.append({
                "basis": "review",
                "label": "本次判断的补充结果",
                "material_seq": material["seq"],
                "side": material["side"],
                "review_version": material["member_snapshot_version"],
                "peer_trigger_version": material["peer_trigger_version"],
                "monday_a": monday_a,
                "monday_b": monday_b,
                "monday_diff_percentage": samples.monday_diff(material["sample"], peer_sample),
            })

        # 结论：周一差值与触发当班差值同向且仍超过阈值则维持异常，否则建议解除。
        threshold = float(snapshot.get("threshold") or 30.0)
        latest_check = checks[-1]
        conclusion = "维持异常" if latest_check["monday_diff_percentage"] >= threshold else "建议解除告警"

        return {
            "snapshot_id": snapshot["snapshot_id"],
            "anomaly_id": anomaly_id,
            "observation_window": window,
            "monday_date": samples.window_days(window)[0],
            "threshold": threshold,
            "checks": checks,
            "conclusion": conclusion,
        }

    def list_review_snapshots(self) -> list[dict[str, Any]]:
        """列出全部异常复核快照（触发留档 + 复查材料计数）。"""
        result = []
        for snapshot in store.list_review_snapshots():
            result.append({
                "snapshot_id": snapshot["snapshot_id"],
                "anomaly_id": snapshot["anomaly_id"],
                "observation_window": snapshot["observation_window"],
                "source_task_name": snapshot["source_task_name"],
                "acknowledged_by": snapshot["acknowledged_by"],
                "acknowledged_at": snapshot["acknowledged_at"],
                "frozen": snapshot["frozen"],
                "trigger_diff_percentage": snapshot["trigger_record"]["original_diff_percentage"],
                "review_material_count": len(snapshot["review_materials"]),
            })
        return result

    def get_anomaly_report(self, metric_name: str | None = None) -> list[dict[str, Any]]:
        """获取异常报告。

        跨模块数据流: anomaly_detector -> query_journal（异常记录关联）
        """
        records = store.anomaly_records()
        if metric_name:
            records = [r for r in records if metric_name in str(r.get("描述", ""))]

        # 关联查询日志
        for record in records:
            metric_id = record.get("指标ID")
            related_logs = [log for log in store.query_logs()
                            if f"metric_{metric_id}" in str(log.get("涉及指标", ""))
                            and "异常" in str(log.get("查询语句摘要", ""))]
            record["related_queries"] = len(related_logs)

        return records

    def get_detection_coverage(self) -> dict[str, Any]:
        """获取异常检测覆盖率统计。"""
        rules = store.rows(self.MODULE)
        enabled = [r for r in rules if r.get("status") == "已启用"]
        anomalies = store.anomaly_records()

        return {
            "total_rules": len(rules),
            "enabled_rules": len(enabled),
            "disabled_rules": len(rules) - len(enabled),
            "total_anomalies_detected": len(anomalies),
            "coverage_ratio": round(len(enabled) / max(1, len(rules)), 2) if rules else 0,
        }
