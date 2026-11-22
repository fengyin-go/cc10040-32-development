"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
同时提供跨模块查询和数据流处理能力。
"""
from __future__ import annotations

import time
import threading
from typing import Any

from app.seed import SEED_ROWS


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        self._id_counters: dict[str, int] = {}
        # Cache layer storage
        self._cache: dict[str, tuple[float, Any]] = {}
        # Cross-module journal
        self._query_log: list[dict[str, Any]] = []
        # Cohort snapshots：cohort_id -> 版本号 -> 成员快照（追加留档，不再覆盖）
        self._cohort_snapshots: dict[str, list[dict[str, Any]]] = {}
        # 每个分群当前成员快照版本号
        self._cohort_versions: dict[str, int] = {}
        # Anomaly records
        self._anomaly_records: list[dict[str, Any]] = []
        # 异常复核快照：按告警身份与观察窗绑定，触发留档不可变、复查材料只追加
        self._review_snapshots: dict[str, dict[str, Any]] = {}
        # Recalc history
        self._recalc_history: list[dict[str, Any]] = []
        # Lock for thread safety
        self._lock = threading.Lock()

    @property
    def lock(self):
        return self._lock

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def next_id(self, module: str) -> int:
        rows = self.rows(module)
        if not rows:
            return 1
        return max(int(row.get("id", 0)) for row in rows) + 1

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}

    # ── Cross-module query helpers ──

    def find_by_field(self, module: str, field: str, value: Any) -> list[dict[str, Any]]:
        """查找模块中 field 等于 value 的所有记录。"""
        return [row for row in self.rows(module) if row.get(field) == value]

    def find_by_status(self, module: str, status: str) -> list[dict[str, Any]]:
        return [row for row in self.rows(module) if row.get("status") == status]

    # ── Query journal ──

    def log_query(self, metric_id: int, operator: str, sql_summary: str,
                  row_count: int, duration_ms: float, status: str = "正常") -> dict[str, Any]:
        entry = {
            "id": len(self._query_log) + 1,
            "查询时间": time.strftime("%Y-%m-%d %H:%M:%S"),
            "操作人": operator,
            "涉及指标": f"metric_{metric_id}",
            "查询语句摘要": sql_summary,
            "返回行数": str(row_count),
            "status": status,
            "duration_ms": duration_ms,
            "pending": status != "正常",
            "abnormal": status in ("慢查询", "异常终止"),
        }
        self._query_log.append(entry)
        return entry

    def query_logs(self) -> list[dict[str, Any]]:
        return self._query_log

    # ── Cache operations ──

    def cache_get(self, key: str) -> Any | None:
        """获取缓存，过期返回 None。"""
        entry = self._cache.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.time() > expires_at:
            del self._cache[key]
            return None
        return value

    def cache_set(self, key: str, value: Any, ttl: int) -> None:
        """设置缓存，ttl 单位为秒。"""
        self._cache[key] = (time.time() + ttl, value)

    def cache_invalidate(self, key: str) -> bool:
        return self._cache.pop(key, None) is not None

    def cache_invalidate_by_prefix(self, prefix: str) -> int:
        count = 0
        for key in list(self._cache):
            if key.startswith(prefix):
                del self._cache[key]
                count += 1
        return count

    # ── Cohort operations（成员快照按版本追加留档） ──

    def save_cohort_snapshot(self, cohort_id: int, members: list[dict[str, Any]],
                             *, source: str = "recompute") -> dict[str, Any]:
        """保存一份新的成员快照，版本号递增，历史版本不覆盖。

        source 标明快照来源：recompute=分群重算，review=异常复查另取材料。
        返回带版本号的快照描述符。
        """
        key = str(cohort_id)
        history = self._cohort_snapshots.setdefault(key, [])
        version = self._cohort_versions.get(key, 0) + 1
        snapshot = {
            "cohort_id": cohort_id,
            "version": version,
            "source": source,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "members": members,
            "member_count": len(members),
        }
        history.append(snapshot)
        self._cohort_versions[key] = version
        return snapshot

    def get_cohort_snapshot(self, cohort_id: int) -> list[dict[str, Any]] | None:
        """兼容旧调用：返回最新版本的成员列表。"""
        history = self._cohort_snapshots.get(str(cohort_id))
        if not history:
            return None
        return history[-1]["members"]

    def current_cohort_version(self, cohort_id: int) -> int:
        return self._cohort_versions.get(str(cohort_id), 0)

    def get_cohort_snapshot_versioned(self, cohort_id: int,
                                      version: int) -> dict[str, Any] | None:
        """按版本号取回某一版成员快照，找不到返回 None。"""
        for snapshot in self._cohort_snapshots.get(str(cohort_id), []):
            if snapshot["version"] == version:
                return snapshot
        return None

    def get_latest_cohort_snapshot(self, cohort_id: int) -> dict[str, Any] | None:
        history = self._cohort_snapshots.get(str(cohort_id))
        return history[-1] if history else None

    def cohort_snapshot_history(self, cohort_id: int) -> list[dict[str, Any]]:
        """返回某分群全部成员快照版本（不含成员明细，便于列表展示）。"""
        return [
            {k: v for k, v in snapshot.items() if k != "members"}
            for snapshot in self._cohort_snapshots.get(str(cohort_id), [])
        ]

    # ── 异常复核快照（按告警身份 + 观察窗绑定） ──

    @staticmethod
    def review_key(anomaly_id: int, window: str) -> str:
        return f"anomaly_{anomaly_id}@{window}"

    def save_review_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """持久化触发资料；同一告警同一观察窗只允许建立一次触发留档。"""
        key = self.review_key(snapshot["anomaly_id"], snapshot["observation_window"])
        if key in self._review_snapshots:
            existing = self._review_snapshots[key]
            if existing.get("frozen"):
                raise ValueError(
                    f"告警 {snapshot['anomaly_id']} 在观察窗 {snapshot['observation_window']} "
                    "的触发留档已冻结，不得覆盖")
        snapshot.setdefault("review_materials", [])
        snapshot["frozen"] = True
        self._review_snapshots[key] = snapshot
        return snapshot

    def get_review_snapshot(self, anomaly_id: int, window: str) -> dict[str, Any] | None:
        return self._review_snapshots.get(self.review_key(anomaly_id, window))

    def list_review_snapshots(self) -> list[dict[str, Any]]:
        return list(self._review_snapshots.values())

    def append_review_material(self, anomaly_id: int, window: str,
                               material: dict[str, Any]) -> dict[str, Any] | None:
        """向既有复核快照追加复查材料；触发留档保持不变。"""
        snapshot = self.get_review_snapshot(anomaly_id, window)
        if snapshot is None:
            return None
        material["seq"] = len(snapshot["review_materials"]) + 1
        material["appended_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        snapshot["review_materials"].append(material)
        return snapshot

    # ── Anomaly detection operations ──

    def record_anomaly(self, metric_id: int, severity: str, description: str,
                       value: float, threshold: float) -> dict[str, Any]:
        record = {
            "id": len(self._anomaly_records) + 1,
            "指标ID": metric_id,
            "严重程度": severity,
            "描述": description,
            "当前值": value,
            "阈值": threshold,
            "时间": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._anomaly_records.append(record)
        return record

    def anomaly_records(self) -> list[dict[str, Any]]:
        return self._anomaly_records

    # ── Recalc operations ──

    def record_recalc(self, metric_id: int, old_version: str, new_version: str,
                      time_range: str, diff_summary: str) -> dict[str, Any]:
        record = {
            "id": len(self._recalc_history) + 1,
            "关联指标": f"metric_{metric_id}",
            "旧口径": old_version,
            "新口径": new_version,
            "历史时间范围": time_range,
            "产出差异": diff_summary,
            "重算时间": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._recalc_history.append(record)
        return record

    def recalc_history(self) -> list[dict[str, Any]]:
        return self._recalc_history

    # ── Data artifacts for cross-module consumption ──

    def get_metric_data(self, metric_id: int) -> dict[str, Any] | None:
        """Get metric data with its calibers, queries, and services."""
        metric = self.find("metric_definition", metric_id)
        if metric is None:
            return None
        return {
            "metric": metric,
            "calibers": self.find_by_field("caliber_version", "所属指标",
                                           metric.get("指标名称", "")),
            "queries": self.find_by_field("query_builder", "关联指标",
                                          metric.get("指标名称", "")),
            "services": [row for row in self.rows("data_service")
                         if metric.get("指标名称", "") in str(row.get("关联指标或模型", ""))],
            "subscriptions": self.find_by_field("data_subscription", "关联指标",
                                                 metric.get("指标名称", "")),
        }

    def get_dimension_model_data(self, model_id: int) -> dict[str, Any] | None:
        """Get dimension model with its drill-down paths and pivot configs."""
        model = self.find("dimension_model", model_id)
        if model is None:
            return None
        return {
            "model": model,
            "drill_paths": self.find_by_field("drill_down", "起始指标",
                                              model.get("模型名称", "")),
            "pivot_configs": self.find_by_field("pivot_table", "数据源指标",
                                                 model.get("模型名称", "")),
        }

    def get_cohort_analysis(self, cohort_id: int) -> dict[str, Any] | None:
        """Get cohort with experiments and segment comparisons."""
        cohort = self.find("user_cohort", cohort_id)
        if cohort is None:
            return None
        cohort_name = cohort.get("分群名称", "")
        return {
            "cohort": cohort,
            "experiments": [row for row in self.rows("experiment_group")
                            if cohort_name in str(row.get("筛选条件", ""))],
            "comparisons": [row for row in self.rows("segment_compare")
                            if cohort_name in (str(row.get("分群A", "")) + str(row.get("分群B", "")))],
            "anomaly_rules": [row for row in self.rows("anomaly_detector")
                              if cohort_name in str(row.get("目标指标", ""))],
        }

    def get_field_lineage(self, field_name: str) -> dict[str, Any]:
        """Trace a field's complete lineage chain."""
        entries = self.find_by_field("field_lineage", "目标字段", field_name)
        if not entries:
            return {"field": field_name, "lineage": [], "depth": 0}

        lineage = [{
            "source_fields": entry.get("来源字段列表", ""),
            "transform": entry.get("转换逻辑", ""),
            "intermediate_table": entry.get("中间表", ""),
            "depth": entry.get("血缘深度", ""),
            "status": entry.get("status", ""),
        } for entry in entries]

        return {
            "field": field_name,
            "lineage": lineage,
            "depth": len(lineage),
        }


store = Store()