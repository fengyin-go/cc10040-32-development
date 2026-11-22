"""异常复核快照业务行为测试。

覆盖需求链路:
1. 分群对比触发告警，异常记录携带观察窗与两组成员快照版本；
2. 签收告警时建立异常复核快照，读取触发时两组样本并绑定名单版本；
3. 触发资料按告警身份 + 观察窗持久化且冻结，重复签收/重复保存不得覆盖；
4. 复查时另取材料：归档新成员快照、追加不覆盖，且能追到另一组所用版本；
5. 打开复核快照依据触发时两份样本复算当班差值（原差值复算一致）；
6. 成员重算后版本区分清晰：trigger 与 review 材料可分辨；
7. 按周一采样数据核对复查结论；
8. 签收两组告警后实验分群完成新一轮计算。
"""
from __future__ import annotations

import copy

import pytest

from app.store import store
from app.services.user_cohort import UserCohortService
from app.services.segment_compare import SegmentCompareService
from app.services.anomaly_detector import AnomalyDetectorService
from app.services import anomaly_samples as samples


WINDOW = "2026-11-16"  # 周一，观察窗 2026-11-16 ~ 2026-11-22


@pytest.fixture(autouse=True)
def isolate_store():
    """每个用例前后还原内存仓库，避免用例间共享状态污染。"""
    saved = copy.deepcopy({
        "tables": store._tables,
        "query_log": store._query_log,
        "cohort_snapshots": store._cohort_snapshots,
        "cohort_versions": store._cohort_versions,
        "anomaly_records": store._anomaly_records,
        "review_snapshots": store._review_snapshots,
        "recalc_history": store._recalc_history,
        "cache": store._cache,
    })
    yield
    store._tables = saved["tables"]
    store._query_log = saved["query_log"]
    store._cohort_snapshots = saved["cohort_snapshots"]
    store._cohort_versions = saved["cohort_versions"]
    store._anomaly_records = saved["anomaly_records"]
    store._review_snapshots = saved["review_snapshots"]
    store._recalc_history = saved["recalc_history"]
    store._cache = saved["cache"]


@pytest.fixture
def review_fixture():
    """构造两组真实分群、引用它们的对比任务和实验分群规则。"""
    cohort_rows = store.rows("user_cohort")
    a_id = max(int(r["id"]) for r in cohort_rows) + 1
    b_id = a_id + 1
    cohort_rows.extend([
        {"id": a_id, "status": "待计算", "pending": True, "abnormal": False,
         "分群名称": "复核实验组A", "分群条件": "复核实验条件",
         "群组大小": "100", "更新频率": "每日", "最近更新时间": "2026-11-15"},
        {"id": b_id, "status": "待计算", "pending": True, "abnormal": False,
         "分群名称": "复核实验组B", "分群条件": "复核实验条件",
         "群组大小": "40", "更新频率": "每日", "最近更新时间": "2026-11-15"},
    ])

    compare_rows = store.rows("segment_compare")
    task_id = max(int(r["id"]) for r in compare_rows) + 1
    compare_rows.append({
        "id": task_id, "status": "待执行", "pending": True, "abnormal": False,
        "对比名称": "复核两组对比", "分群A": "复核实验组A", "分群B": "复核实验组B",
        "对比维度": "活跃率", "对比结果摘要": "",
    })

    exp_rows = store.rows("experiment_group")
    exp_id = max(int(r["id"]) for r in exp_rows) + 1
    exp_rows.append({
        "id": exp_id, "status": "待生效", "pending": True, "abnormal": False,
        "规则名称": "复核联动实验", "筛选条件": "基于 复核实验组A 与 复核实验组B",
        "群组大小": "100", "实验周期": "7天", "对照群标识": "是",
    })

    # 两组先各归档一次成员快照（v1），让触发留档绑定到具体版本。
    UserCohortService().recompute_cohort(a_id)
    UserCohortService().recompute_cohort(b_id)

    return {"a_id": a_id, "b_id": b_id, "task_id": task_id, "exp_id": exp_id}


class TestAnomalyReviewSnapshot:

    def test_compare_triggers_anomaly_with_window_and_versions(self, review_fixture):
        """执行对比产生告警，异常记录含观察窗与两组版本。"""
        svc = SegmentCompareService()
        result = svc.execute_compare(review_fixture["task_id"], window=WINDOW)

        assert result["anomaly_detected"] is True
        anomaly_id = result["anomaly_id"]
        assert anomaly_id is not None
        assert result["observation_window"] == WINDOW
        assert result["cohort_a"]["member_snapshot_version"] == 1
        assert result["cohort_b"]["member_snapshot_version"] == 1

        anomaly = next(a for a in store.anomaly_records() if int(a["id"]) == anomaly_id)
        assert anomaly["观察窗"] == WINDOW
        assert anomaly["成员版本A"] == 1
        assert anomaly["成员版本B"] == 1
        assert anomaly["分群A"] == "复核实验组A"
        assert anomaly["分群B"] == "复核实验组B"

    def test_acknowledge_creates_snapshot_bound_to_two_lists(self, review_fixture):
        """签收：建立复核快照，触发样本 + 两组成员版本全部绑定。"""
        compare = SegmentCompareService().execute_compare(
            review_fixture["task_id"], window=WINDOW)
        anomaly_id = compare["anomaly_id"]

        result = AnomalyDetectorService().acknowledge_anomaly(
            anomaly_id, operator="值班员甲", window=WINDOW)

        assert "error" not in result
        snapshot = store.get_review_snapshot(anomaly_id, WINDOW)
        assert snapshot is not None
        assert snapshot["snapshot_id"] == result["snapshot_id"]
        assert snapshot["frozen"] is True
        trigger = snapshot["trigger_record"]
        assert trigger["material_kind"] == "trigger"
        assert trigger["cohort_a"]["cohort_name"] == "复核实验组A"
        assert trigger["cohort_b"]["cohort_name"] == "复核实验组B"
        assert trigger["cohort_a"]["member_snapshot_version"] == 1
        assert trigger["cohort_b"]["member_snapshot_version"] == 1
        # 两组触发样本齐备
        assert trigger["cohort_a"]["sample"]["sample_size"] == 7
        assert trigger["cohort_b"]["sample"]["sample_size"] == 7
        # 按告警身份 + 观察窗绑定
        assert store.review_key(anomaly_id, WINDOW) in store._review_snapshots

    def test_trigger_record_persisted_and_frozen_no_overwrite(self, review_fixture):
        """触发留档不可覆盖：重复签收报错、直接重复保存抛错。"""
        anomaly_id = SegmentCompareService().execute_compare(
            review_fixture["task_id"], window=WINDOW)["anomaly_id"]
        svc = AnomalyDetectorService()
        first = svc.acknowledge_anomaly(anomaly_id, window=WINDOW)
        again = svc.acknowledge_anomaly(anomaly_id, window=WINDOW)

        assert "error" in again
        assert again["snapshot_id"] == first["snapshot_id"]

        snapshot = store.get_review_snapshot(anomaly_id, WINDOW)
        with pytest.raises(ValueError):
            store.save_review_snapshot(dict(snapshot))

        # 原触发记录字段保持不变
        assert snapshot["trigger_record"]["cohort_a"]["member_snapshot_version"] == 1

    def test_same_anomaly_different_window_is_separate_binding(self, review_fixture):
        """同一告警不同观察窗分别绑定，互不覆盖。"""
        anomaly_id = SegmentCompareService().execute_compare(
            review_fixture["task_id"], window=WINDOW)["anomaly_id"]
        svc = AnomalyDetectorService()
        first = svc.acknowledge_anomaly(anomaly_id, window=WINDOW)
        other_window = "2026-11-23"
        second = svc.acknowledge_anomaly(anomaly_id, window=other_window)

        assert "error" not in second
        assert first["snapshot_id"] != second["snapshot_id"]
        assert store.get_review_snapshot(anomaly_id, WINDOW) is not None
        assert store.get_review_snapshot(anomaly_id, other_window) is not None

    def test_acknowledge_triggers_experiment_new_round(self, review_fixture):
        """签收有两组数据的告警后，实验分群完成新一轮计算。"""
        anomaly_id = SegmentCompareService().execute_compare(
            review_fixture["task_id"], window=WINDOW)["anomaly_id"]
        result = AnomalyDetectorService().acknowledge_anomaly(anomaly_id, window=WINDOW)

        new_round = result["experiment_new_round"]
        assert new_round["affected_count"] >= 1
        group = store.find("experiment_group", review_fixture["exp_id"])
        assert int(group["计算轮次"]) == 1
        assert group["最近一轮触发"] == result["snapshot_id"]
        assert group["status"] == "已生效"

        # 再签收另一观察窗，轮次继续递增
        second = AnomalyDetectorService().acknowledge_anomaly(
            anomaly_id, window="2026-11-23")
        assert int(second["experiment_new_round"]["affected_groups"][0]["round"]) == 2

    def test_open_snapshot_recalcs_original_diff(self, review_fixture):
        """打开复核快照：依据触发时两份样本复算当班差值，与原差值一致。"""
        anomaly_id = SegmentCompareService().execute_compare(
            review_fixture["task_id"], window=WINDOW)["anomaly_id"]
        svc = AnomalyDetectorService()
        ack = svc.acknowledge_anomaly(anomaly_id, window=WINDOW)

        opened = svc.open_review_snapshot(anomaly_id, WINDOW)
        trigger = opened["trigger_record"]
        # 复算差值与签收时留档的原差值相等
        assert trigger["recalc_diff_percentage"] == ack["original_diff_percentage"]
        assert trigger["recalc_diff_percentage"] == trigger["original_diff_percentage"]

        # 与样本工具直接复算结果一致
        snapshot = store.get_review_snapshot(anomaly_id, WINDOW)
        direct = samples.diff_percentage(
            snapshot["trigger_record"]["cohort_a"]["sample"],
            snapshot["trigger_record"]["cohort_b"]["sample"])
        assert trigger["recalc_diff_percentage"] == direct

    def test_segment_compare_opens_anomaly_review(self, review_fixture):
        """分群对比分析侧也能打开异常复核快照。"""
        anomaly_id = SegmentCompareService().execute_compare(
            review_fixture["task_id"], window=WINDOW)["anomaly_id"]
        AnomalyDetectorService().acknowledge_anomaly(anomaly_id, window=WINDOW)

        opened = SegmentCompareService().open_anomaly_review(anomaly_id, WINDOW)
        assert "error" not in opened
        assert opened["snapshot_id"] == f"RS-{anomaly_id}-{WINDOW}"
        assert "recalc_diff_percentage" in opened["trigger_record"]

    def test_append_review_material_versions_and_peer_trace(self, review_fixture):
        """复查另取材料：新成员快照版本递增、追加不覆盖、可追另一组版本。"""
        anomaly_id = SegmentCompareService().execute_compare(
            review_fixture["task_id"], window=WINDOW)["anomaly_id"]
        svc = AnomalyDetectorService()
        svc.acknowledge_anomaly(anomaly_id, window=WINDOW)

        before_versions = store.cohort_snapshot_history(review_fixture["a_id"])
        assert [v["version"] for v in before_versions] == [1]

        material = svc.append_review_material(
            anomaly_id, WINDOW, cohort_name="复核实验组A",
            operator="值班员乙", note="周一补采")
        assert "error" not in material

        # A 组归档了新版本 v2，历史版本保留
        history = store.cohort_snapshot_history(review_fixture["a_id"])
        assert [v["version"] for v in history] == [1, 2]
        assert history[-1]["source"] == "review"

        snapshot = store.get_review_snapshot(anomaly_id, WINDOW)
        assert len(snapshot["review_materials"]) == 1
        added = snapshot["review_materials"][0]
        assert added["material_kind"] == "review"
        assert added["member_snapshot_version"] == 2
        # 同组触发时所用版本、另一组触发版本均可追溯
        assert added["trigger_version_of_same_side"] == 1
        assert added["peer_cohort_name"] == "复核实验组B"
        assert added["peer_trigger_version"] == 1

        # 触发留档未被覆盖
        assert snapshot["trigger_record"]["cohort_a"]["member_snapshot_version"] == 1
        assert snapshot["frozen"] is True

    def test_multiple_reviews_between_recomputes_keep_versions_distinct(self, review_fixture):
        """两次复核间成员快照重算：每份材料标清 trigger/review 与版本。"""
        anomaly_id = SegmentCompareService().execute_compare(
            review_fixture["task_id"], window=WINDOW)["anomaly_id"]
        svc = AnomalyDetectorService()
        svc.acknowledge_anomaly(anomaly_id, window=WINDOW)

        svc.append_review_material(anomaly_id, WINDOW, cohort_name="复核实验组A")
        svc.append_review_material(anomaly_id, WINDOW, cohort_name="复核实验组B")

        opened = svc.open_review_snapshot(anomaly_id, WINDOW)
        assert opened["trigger_record"]["material_kind"] == "trigger"
        assert len(opened["review_materials"]) == 2
        sides = {m["side"]: m for m in opened["review_materials"]}
        # A 已重算到 v2，B 也重算到 v2；触发留档仍是 v1
        assert sides["cohort_a"]["member_snapshot_version"] == 2
        assert sides["cohort_b"]["member_snapshot_version"] == 2
        assert sides["cohort_a"]["trigger_version_of_same_side"] == 1
        # A 的复查材料对照 B 的触发版本 v1
        assert sides["cohort_a"]["peer_trigger_version"] == 1
        # 每份材料都带与对组触发样本的复算差值
        assert "recalc_diff_against_trigger_peer" in sides["cohort_a"]
        assert "recalc_diff_against_trigger_peer" in sides["cohort_b"]

    def test_review_material_without_snapshot_rejected(self, review_fixture):
        """未签收先追加材料应被拒绝。"""
        anomaly_id = SegmentCompareService().execute_compare(
            review_fixture["task_id"], window=WINDOW)["anomaly_id"]
        result = AnomalyDetectorService().append_review_material(
            anomaly_id, WINDOW, cohort_name="复核实验组A")
        assert "error" in result

    def test_monday_check_distinguishes_basis_and_gives_conclusion(self, review_fixture):
        """按周一采样核对：trigger 与 review 材料分列，给出复查结论。"""
        anomaly_id = SegmentCompareService().execute_compare(
            review_fixture["task_id"], window=WINDOW)["anomaly_id"]
        svc = AnomalyDetectorService()
        svc.acknowledge_anomaly(anomaly_id, window=WINDOW)
        svc.append_review_material(anomaly_id, WINDOW, cohort_name="复核实验组A")

        check = svc.verify_with_monday_sample(anomaly_id, WINDOW)
        assert check["monday_date"] == "2026-11-16"
        labels = [c["label"] for c in check["checks"]]
        assert "原告警所用样本" in labels
        assert "本次判断的补充结果" in labels

        trigger_check = check["checks"][0]
        assert trigger_check["basis"] == "trigger"
        review_check = check["checks"][1]
        assert review_check["basis"] == "review"
        assert review_check["review_version"] == 2
        assert review_check["peer_trigger_version"] == 1
        assert check["conclusion"] in ("维持异常", "建议解除告警")

        # 周一差值与工具单点复算一致
        snapshot = store.get_review_snapshot(anomaly_id, WINDOW)
        direct = samples.monday_diff(
            snapshot["trigger_record"]["cohort_a"]["sample"],
            snapshot["trigger_record"]["cohort_b"]["sample"])
        assert trigger_check["monday_diff_percentage"] == direct

    def test_segment_compare_monday_check_endpoint_logic(self, review_fixture):
        """分群对比分析侧的周一核对入口返回同样结构。"""
        anomaly_id = SegmentCompareService().execute_compare(
            review_fixture["task_id"], window=WINDOW)["anomaly_id"]
        AnomalyDetectorService().acknowledge_anomaly(anomaly_id, window=WINDOW)
        check = SegmentCompareService().verify_review_by_monday(anomaly_id, WINDOW)
        assert "checks" in check and "conclusion" in check

    def test_list_review_snapshots(self, review_fixture):
        """快照清单含触发差值与复查材料计数。"""
        anomaly_id = SegmentCompareService().execute_compare(
            review_fixture["task_id"], window=WINDOW)["anomaly_id"]
        svc = AnomalyDetectorService()
        svc.acknowledge_anomaly(anomaly_id, window=WINDOW)
        svc.append_review_material(anomaly_id, WINDOW, cohort_name="复核实验组A")

        items = svc.list_review_snapshots()
        mine = [i for i in items if i["anomaly_id"] == anomaly_id and
                i["observation_window"] == WINDOW]
        assert len(mine) == 1
        assert mine[0]["frozen"] is True
        assert mine[0]["review_material_count"] == 1
        assert "trigger_diff_percentage" in mine[0]

    def test_samples_are_deterministic_per_version_and_kind(self):
        """同版本同材料类型样本稳定；trigger/review 与不同版本可区分。"""
        s1 = samples.build_cohort_samples("X", WINDOW, 100, "v1", material_kind="trigger")
        s2 = samples.build_cohort_samples("X", WINDOW, 100, "v1", material_kind="trigger")
        s_review = samples.build_cohort_samples("X", WINDOW, 100, "v2", material_kind="review")
        assert s1["daily"] == s2["daily"]
        assert s1["material_kind"] != s_review["material_kind"]
        assert s1["version"] != s_review["version"]
        assert samples.window_days(WINDOW)[0] == "2026-11-16"
        assert samples.current_observation_window(
            __import__("datetime").date(2026, 11, 22)) == "2026-11-16"
