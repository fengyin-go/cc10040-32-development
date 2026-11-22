"""跨模块数据流API路由：暴露跨模块数据流链路。

链1: metric_definition -> caliber_version -> query_builder -> data_service -> cache_layer -> query_journal
链2: dimension_model -> drill_down -> pivot_table -> report_template
链3: user_cohort -> experiment_group -> segment_compare -> anomaly_detector
"""
from __future__ import annotations

from fastapi import APIRouter

from app.services.metric_definition import MetricDefinitionService
from app.services.caliber_version import CaliberVersionService
from app.services.query_builder import QueryBuilderService
from app.services.data_service import DataServiceService
from app.services.cache_layer import CacheLayerService
from app.services.query_journal import QueryJournalService
from app.services.dimension_model import DimensionModelService
from app.services.drill_down import DrillDownService
from app.services.pivot_table import PivotTableService
from app.services.report_template import ReportTemplateService
from app.services.user_cohort import UserCohortService
from app.services.experiment_group import ExperimentGroupService
from app.services.segment_compare import SegmentCompareService
from app.services.anomaly_detector import AnomalyDetectorService
from app.services.recalc_engine import RecalcEngineService
from app.services.field_lineage import FieldLineageService

router = APIRouter(prefix="/api/cross", tags=["跨模块数据流"])


# ── 链1: 指标口径查询链 ──

@router.get("/metric-chain/{metric_id}")
def get_metric_chain(metric_id: int):
    """获取指标完整数据链：指标 -> 口径 -> 查询模板 -> 数据服务 -> 缓存 -> 日志"""
    svc = MetricDefinitionService()
    return svc.get_metric_chain(metric_id)


@router.post("/metric/{metric_id}/publish-with-caliber")
def publish_with_caliber(metric_id: int, caliber_desc: str = "默认计算口径"):
    """发布指标并自动创建口径版本"""
    svc = MetricDefinitionService()
    result, msg = svc.publish_with_caliber(metric_id, caliber_desc)
    return {"ok": result is not None, "message": msg, "entry": result}


@router.post("/caliber/{version_id}/activate")
def activate_caliber(version_id: int):
    """启用口径版本，自动过期旧版本并失效缓存"""
    svc = CaliberVersionService()
    result, msg = svc.activate_version(version_id)
    return {"ok": result is not None, "message": msg, "entry": result}


@router.get("/caliber/{metric_name}/history")
def get_caliber_history(metric_name: str):
    """获取指标的口径版本历史"""
    svc = CaliberVersionService()
    return svc.get_version_history(metric_name)


@router.post("/query/{template_id}/execute")
def execute_query(template_id: int, operator: str = "系统"):
    """执行查询模板，返回结果与日志"""
    svc = QueryBuilderService()
    return svc.execute_query(template_id, operator=operator)


@router.post("/data-service/{api_id}/call")
def call_data_service(api_id: int, operator: str = "客户端"):
    """调用数据服务API"""
    svc = DataServiceService()
    return svc.call_service(api_id, operator=operator)


@router.get("/data-service/{api_id}/health")
def service_health(api_id: int):
    """获取服务接口健康状态"""
    svc = DataServiceService()
    return svc.get_service_health(api_id)


@router.post("/cache/invalidate-by-metric/{metric_name}")
def invalidate_cache_by_metric(metric_name: str):
    """根据指标名称失效缓存"""
    svc = CacheLayerService()
    return svc.invalidate_by_metric(metric_name)


@router.post("/cache/warm/{metric_name}")
def warm_cache(metric_name: str):
    """预热指标查询缓存"""
    svc = CacheLayerService()
    return svc.warm_cache(metric_name)


@router.get("/cache/stats")
def cache_stats():
    """获取缓存统计"""
    svc = CacheLayerService()
    return svc.get_cache_stats()


@router.get("/query-journal/slow-queries")
def slow_queries(threshold_ms: float = 100):
    """获取慢查询列表"""
    svc = QueryJournalService()
    return svc.get_slow_queries(threshold_ms)


@router.get("/query-journal/stats-by-metric")
def query_stats_by_metric():
    """按指标统计查询情况"""
    svc = QueryJournalService()
    return svc.get_query_stats_by_metric()


@router.get("/query-journal/performance-insights")
def performance_insights():
    """获取性能洞察"""
    svc = QueryJournalService()
    return svc.get_performance_insights()


# ── 链2: 维度分析下钻链 ──

@router.get("/dimension-model/{model_id}/with-analytics")
def get_dimension_model_with_analytics(model_id: int):
    """获取维度模型及其关联分析配置"""
    svc = DimensionModelService()
    return svc.get_model_with_analytics(model_id)


@router.get("/dimension-model/{model_id}/hierarchy")
def get_dimension_hierarchy(model_id: int):
    """获取维度层次结构"""
    svc = DimensionModelService()
    return svc.get_dimension_hierarchy(model_id)


@router.post("/drill-down/{path_id}/execute")
def execute_drill_down(path_id: int, current_level: int = 0):
    """执行下钻分析"""
    svc = DrillDownService()
    return svc.execute_drill_down(path_id, current_level)


@router.get("/drill-down/{path_id}/tree")
def get_drill_tree(path_id: int):
    """获取下钻路径树"""
    svc = DrillDownService()
    return svc.get_drill_tree(path_id)


@router.get("/drill-down/{path_id}/export-to-pivot")
def export_drill_to_pivot(path_id: int):
    """将下钻路径导出为透视表配置建议"""
    svc = DrillDownService()
    return svc.export_to_pivot(path_id)


@router.post("/pivot-table/{config_id}/compute")
def compute_pivot(config_id: int):
    """计算透视表"""
    svc = PivotTableService()
    return svc.compute_pivot(config_id)


@router.post("/pivot-table/{config_id}/share-to-report")
def share_pivot_to_report(config_id: int, report_template_id: int):
    """将透视表共享到报告模板"""
    svc = PivotTableService()
    return svc.share_to_report(config_id, report_template_id)


@router.get("/report-template/{template_id}/generate")
def generate_report(template_id: int):
    """生成报告"""
    svc = ReportTemplateService()
    return svc.generate_report(template_id)


# ── 链3: 分群对比异常链 ──

@router.post("/user-cohort/{cohort_id}/recompute")
def recompute_cohort(cohort_id: int):
    """重新计算分群"""
    svc = UserCohortService()
    return svc.recompute_cohort(cohort_id)


@router.get("/user-cohort/{cohort_id}/detail")
def get_cohort_detail(cohort_id: int):
    """获取分群详细信息"""
    svc = UserCohortService()
    return svc.get_cohort_detail(cohort_id)


@router.get("/experiment-group/{group_id}/evaluate")
def evaluate_experiment_groups(group_a_id: int, group_b_id: int):
    """评估两个实验分组"""
    svc = ExperimentGroupService()
    return svc.evaluate_groups(group_a_id, group_b_id)


@router.post("/segment-compare/{task_id}/execute")
def execute_segment_compare(task_id: int):
    """执行分群对比分析"""
    svc = SegmentCompareService()
    return svc.execute_compare(task_id)


@router.get("/segment-compare/{task_id}/insights")
def segment_compare_insights(task_id: int):
    """获取跨分群洞察"""
    svc = SegmentCompareService()
    return svc.get_cross_segment_insights(task_id)


@router.post("/anomaly-detector/{rule_id}/detect")
def run_anomaly_detection(rule_id: int):
    """执行异常检测"""
    svc = AnomalyDetectorService()
    return svc.run_detection(rule_id)


@router.get("/anomaly-detector/report")
def anomaly_report(metric_name: str | None = None):
    """获取异常报告"""
    svc = AnomalyDetectorService()
    return svc.get_anomaly_report(metric_name)


@router.get("/anomaly-detector/coverage")
def detection_coverage():
    """获取异常检测覆盖率"""
    svc = AnomalyDetectorService()
    return svc.get_detection_coverage()


# ── 异常复核快照：签收留档、复查追加、回看复算 ──

@router.post("/anomaly/{anomaly_id}/acknowledge")
def acknowledge_anomaly(anomaly_id: int, operator: str = "值班员",
                        window: str | None = None):
    """签收告警并建立异常复核快照（绑定两组名单及成员快照版本）。"""
    svc = AnomalyDetectorService()
    return svc.acknowledge_anomaly(anomaly_id, operator=operator, window=window)


@router.post("/anomaly/{anomaly_id}/review-material")
def append_review_material(anomaly_id: int, cohort_name: str,
                           window: str | None = None, operator: str = "值班员",
                           note: str = ""):
    """复查时另取材料：归档新成员快照并追加到复核快照，不覆盖触发记录。"""
    svc = AnomalyDetectorService()
    return svc.append_review_material(
        anomaly_id, window or _current_window(),
        cohort_name=cohort_name, operator=operator, note=note)


@router.get("/anomaly/{anomaly_id}/review-snapshot")
def open_review_snapshot(anomaly_id: int, window: str | None = None):
    """打开异常复核快照，依据触发时两份样本复算当班差值。"""
    svc = AnomalyDetectorService()
    return svc.open_review_snapshot(anomaly_id, window)


@router.get("/anomaly/{anomaly_id}/monday-check")
def monday_check(anomaly_id: int, window: str | None = None):
    """按周一采样数据核对复查结论。"""
    svc = AnomalyDetectorService()
    return svc.verify_with_monday_sample(anomaly_id, window)


@router.get("/anomaly/review-snapshots")
def list_review_snapshots():
    """列出全部异常复核快照。"""
    svc = AnomalyDetectorService()
    return {"items": svc.list_review_snapshots()}


@router.post("/segment-compare/{task_id}/anomaly-review/{anomaly_id}")
def segment_open_review(task_id: int, anomaly_id: int, window: str | None = None):
    """分群对比分析打开异常复核快照并复算当班差值。"""
    svc = SegmentCompareService()
    return svc.open_anomaly_review(anomaly_id, window)


@router.post("/segment-compare/{task_id}/anomaly-review/{anomaly_id}/monday-check")
def segment_monday_check(task_id: int, anomaly_id: int, window: str | None = None):
    """分群对比分析按周一采样核对复查结论。"""
    svc = SegmentCompareService()
    return svc.verify_review_by_monday(anomaly_id, window)


@router.post("/experiment-group/new-round")
def experiment_new_round(cohort_a: str, cohort_b: str,
                         trigger_snapshot_id: str = "", operator: str = "值班员"):
    """签收联动：实验分群完成新一轮计算。"""
    svc = ExperimentGroupService()
    return svc.run_new_round(cohort_a, cohort_b,
                             trigger_snapshot_id=trigger_snapshot_id, operator=operator)


def _current_window() -> str:
    from app.services.anomaly_samples import current_observation_window
    return current_observation_window()


# ── 重算引擎 ──

@router.post("/recalc/{task_id}/start")
def start_recalc(task_id: int):
    """启动口径重算"""
    svc = RecalcEngineService()
    return svc.start_recalc(task_id)


@router.get("/recalc/history")
def recalc_history(metric_name: str | None = None):
    """获取重算历史"""
    svc = RecalcEngineService()
    return svc.get_recalc_history(metric_name)


@router.get("/recalc/{task_id}/estimate-impact")
def estimate_recalc_impact(task_id: int):
    """预估算重算影响"""
    svc = RecalcEngineService()
    return svc.estimate_recalc_impact(task_id)


@router.post("/metric/{metric_id}/recalc")
def recalc_metric(metric_id: int, new_caliber_version: str, time_range: str):
    """触发指标历史数据重算"""
    svc = MetricDefinitionService()
    result, msg = svc.recalc_metric_history(metric_id, new_caliber_version, time_range)
    return {"ok": result is not None, "message": msg, "entry": result}


# ── 字段血缘 ──

@router.get("/field-lineage/{field_name}/trace")
def trace_field(field_name: str):
    """追溯字段血缘"""
    svc = FieldLineageService()
    return svc.trace_field(field_name)


@router.get("/field-lineage/{field_name}/downstream-impact")
def field_downstream_impact(field_name: str):
    """分析字段变更下游影响"""
    svc = FieldLineageService()
    return svc.get_downstream_impact(field_name)


# ── 全链路快照 ──

@router.get("/full-chain-snapshot")
def full_chain_snapshot():
    """获取所有跨模块链路的状态快照"""
    return {
        "chain_1_metric_query": {
            "description": "指标口径查询链: metric_definition -> caliber_version -> query_builder -> data_service -> cache_layer -> query_journal",
            "modules": {
                "metric_definition": MetricDefinitionService().domain_snapshot(),
                "caliber_version": CaliberVersionService().domain_snapshot(),
                "query_builder": QueryBuilderService().domain_snapshot(),
                "data_service": DataServiceService().domain_snapshot(),
                "cache_layer": CacheLayerService().domain_snapshot(),
                "query_journal": QueryJournalService().domain_snapshot(),
            },
        },
        "chain_2_dimension_analysis": {
            "description": "维度分析下钻链: dimension_model -> drill_down -> pivot_table -> report_template",
            "modules": {
                "dimension_model": DimensionModelService().domain_snapshot(),
                "drill_down": DrillDownService().domain_snapshot(),
                "pivot_table": PivotTableService().domain_snapshot(),
                "report_template": ReportTemplateService().domain_snapshot(),
            },
        },
        "chain_3_cohort_anomaly": {
            "description": "分群对比异常链: user_cohort -> experiment_group -> segment_compare -> anomaly_detector",
            "modules": {
                "user_cohort": UserCohortService().domain_snapshot(),
                "experiment_group": ExperimentGroupService().domain_snapshot(),
                "segment_compare": SegmentCompareService().domain_snapshot(),
                "anomaly_detector": AnomalyDetectorService().domain_snapshot(),
            },
        },
        "engines": {
            "recalc_engine": RecalcEngineService().domain_snapshot(),
            "field_lineage": FieldLineageService().domain_snapshot(),
        },
    }