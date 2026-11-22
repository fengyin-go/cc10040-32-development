"""用户分群领域服务：管理用户分群定义、计算和分群结果存储。

跨模块数据流: user_cohort -> experiment_group -> segment_compare -> anomaly_detector
分群结果被实验分群、分群对比分析和异常检测器消费。
"""
from __future__ import annotations

import time
from typing import Any

from app.store import store
from app.services.base import BaseModuleService


class UserCohortService(BaseModuleService):
    MODULE = "user_cohort"
    ENTITY = "分群结果"
    OPERATION_TERM = "计算"
    LIST_FIELDS = ["分群名称", "分群条件", "群组大小", "更新频率", "最近更新时间"]
    REQUIRED_FIELDS = ["分群名称", "分群条件", "群组大小"]
    STATUS_ORDER = ["待计算", "已生成", "已失效"]
    ACTION_RULES = {"重新计算": "已生成", "标记失效": "已失效"}
    NEGATIVE_ACTIONS = []

    def recompute_cohort(self, entry_id: int, *, source: str = "recompute",
                         operator: str = "系统") -> dict[str, Any] | None:
        """重新计算分群，生成最新的用户列表快照（按版本追加留档）。

        跨模块数据流: user_cohort -> experiment_group（实验分群引用）
                     user_cohort -> segment_compare（对比分析引用）

        source: recompute=常规重算，review=异常复查时另取复查材料。
        """
        cohort = store.find(self.MODULE, entry_id)
        if cohort is None:
            return {"error": f"分群结果 {entry_id} 不存在"}

        cohort_name = cohort.get("分群名称", "")
        condition = cohort.get("分群条件", "")
        try:
            group_size = int(cohort.get("群组大小", "100"))
        except (ValueError, TypeError):
            group_size = 100

        # 模拟分群计算 - 真实项目会查询数据源
        members = [
            {"user_id": f"U{i+1000}", "name": f"用户{i+1}",
             "cohort": cohort_name, "condition": condition,
             "join_time": time.strftime("%Y-%m-%d")}
            for i in range(min(group_size, 100))
        ]

        # 更新分群基本信息
        cohort["群组大小"] = str(len(members))
        cohort["最近更新时间"] = time.strftime("%Y-%m-%d %H:%M:%S")
        cohort["status"] = "已生成"
        cohort["pending"] = False

        # 保存版本化快照（历史版本不覆盖）
        snapshot = store.save_cohort_snapshot(entry_id, members, source=source)

        # 检查是否有引用的实验分组需要更新
        experiment_refs = store.find_by_field("experiment_group", "筛选条件", cohort_name)
        affected_experiments = [e.get("规则名称") for e in experiment_refs]

        return {
            "cohort_id": entry_id,
            "cohort_name": cohort_name,
            "condition": condition,
            "member_count": len(members),
            "snapshot_version": snapshot["version"],
            "snapshot_source": snapshot["source"],
            "operator": operator,
            "updated_at": cohort.get("最近更新时间"),
            "affected_experiments": affected_experiments,
            "sample_members": members[:5],
        }

    def get_cohort_detail(self, entry_id: int) -> dict[str, Any] | None:
        """获取分群详细信息，包括被哪些实验和对比分析引用。

        跨模块数据流: user_cohort -> experiment_group -> segment_compare
        """
        cohort_data = store.get_cohort_analysis(entry_id)
        if cohort_data is None:
            return None

        cohort = cohort_data["cohort"]
        members = store.get_cohort_snapshot(entry_id)

        return {
            "cohort_id": entry_id,
            "cohort_name": cohort.get("分群名称"),
            "condition": cohort.get("分群条件"),
            "group_size": cohort.get("群组大小"),
            "update_frequency": cohort.get("更新频率"),
            "status": cohort.get("status"),
            "member_count": len(members) if members else 0,
            "referenced_by_experiments": [
                {"name": e.get("规则名称"), "id": e.get("id")}
                for e in cohort_data["experiments"]
            ],
            "referenced_by_comparisons": [
                {"name": c.get("对比名称"), "id": c.get("id")}
                for c in cohort_data["comparisons"]
            ],
            "referenced_by_anomaly_rules": len(cohort_data["anomaly_rules"]),
        }