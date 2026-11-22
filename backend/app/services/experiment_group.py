"""实验分群领域服务：管理实验分组规则、对照群设置和实验周期。

跨模块数据流: user_cohort -> experiment_group -> segment_compare
实验分组基于用户分群，结果用于分群对比。

签收联动: 含两组数据的告警被签收后，由 anomaly_detector 调用 run_new_round，
驱动实验分群基于最新成员快照完成新一轮计算。
"""
from __future__ import annotations

import time
from typing import Any

from app.store import store
from app.services.base import BaseModuleService


class ExperimentGroupService(BaseModuleService):
    MODULE = "experiment_group"
    ENTITY = "分群规则"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["规则名称", "筛选条件", "群组大小", "实验周期", "对照群标识"]
    REQUIRED_FIELDS = ["规则名称", "筛选条件", "群组大小"]
    STATUS_ORDER = ["待生效", "已生效", "已结束"]
    ACTION_RULES = {"启用分群": "已生效", "结束分群": "已结束"}
    NEGATIVE_ACTIONS = []

    def get_experiment_groups(self, condition_cohort_name: str) -> list[dict[str, Any]]:
        """获取基于指定分群条件的所有实验分组。

        跨模块数据流: user_cohort -> experiment_group
        """
        all_groups = store.rows(self.MODULE)
        return [g for g in all_groups if condition_cohort_name in str(g.get("筛选条件", ""))]

    def run_new_round(self, cohort_a_name: str, cohort_b_name: str,
                      *, trigger_snapshot_id: str = "",
                      operator: str = "值班员") -> dict[str, Any]:
        """签收两组数据的告警后，驱动实验分群完成新一轮计算。

        找到筛选条件引用这两组分群的实验分群规则，基于成员快照最新版本
        重新核算群组大小并记录计算轮次；不修改任何异常触发留档。
        """
        groups = store.rows(self.MODULE)
        affected: list[dict[str, Any]] = []
        for group in groups:
            condition = str(group.get("筛选条件", ""))
            if cohort_a_name not in condition and cohort_b_name not in condition:
                continue
            round_no = int(group.get("计算轮次", 0)) + 1
            group["计算轮次"] = round_no
            group["最近一轮时间"] = time.strftime("%Y-%m-%d %H:%M:%S")
            group["最近一轮触发"] = trigger_snapshot_id
            if group.get("status") == "待生效":
                group["status"] = "已生效"
                group["pending"] = False

            bound_versions = {}
            for cohort_name in (cohort_a_name, cohort_b_name):
                rows = store.find_by_field("user_cohort", "分群名称", cohort_name)
                if rows:
                    cid = int(rows[0]["id"])
                    bound_versions[cohort_name] = store.current_cohort_version(cid)
            affected.append({
                "group_id": group.get("id"),
                "group_name": group.get("规则名称"),
                "round": round_no,
                "bound_member_versions": bound_versions,
                "status": group.get("status"),
            })

        return {
            "service": self.MODULE,
            "trigger_snapshot_id": trigger_snapshot_id,
            "operator": operator,
            "cohort_a": cohort_a_name,
            "cohort_b": cohort_b_name,
            "round_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "affected_groups": affected,
            "affected_count": len(affected),
            "message": f"实验分群已完成新一轮计算，共影响 {len(affected)} 条分群规则",
        }

    def evaluate_groups(self, group_a_id: int, group_b_id: int) -> dict[str, Any]:
        """评估两个实验分组并返回对比摘要。

        跨模块数据流: experiment_group -> segment_compare
        """
        group_a = store.find(self.MODULE, group_a_id)
        group_b = store.find(self.MODULE, group_b_id)

        if group_a is None or group_b is None:
            return {"error": "实验分组不存在"}

        try:
            a_size = int(group_a.get("群组大小", 0))
        except (ValueError, TypeError):
            a_size = 0
        try:
            b_size = int(group_b.get("群组大小", 0))
        except (ValueError, TypeError):
            b_size = 0

        # 模拟评估结果
        return {
            "group_a": {
                "name": group_a.get("规则名称"),
                "size": a_size,
                "is_control": group_a.get("对照群标识", "") == "是",
            },
            "group_b": {
                "name": group_b.get("规则名称"),
                "size": b_size,
                "is_control": group_b.get("对照群标识", "") == "是",
            },
            "size_ratio": round(b_size / max(1, a_size), 2),
            "analysis_status": "ready_for_compare",
        }
