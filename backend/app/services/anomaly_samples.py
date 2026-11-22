"""异常样本数据：按观察窗为分群生成触发时与复查时使用的确定性样本。

样本只依赖标准库且对同一组（分群、观察窗、版本）保持稳定，
保证「原告警所用样本」与「复查补充样本」可以被反复回看和复算。
观察窗统一用周一日期（ISO 周）标识，例如 2026-11-16 表示
2026-11-16（周一）至 2026-11-22（周日）这一周。
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from typing import Any

# 平台固定的采样工作日顺序，周一排在第一位用于复查核对。
WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def current_observation_window(today: date | None = None) -> str:
    """返回 today 所在观察窗（ISO 周）的周一日期字符串。"""
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    return monday.strftime("%Y-%m-%d")


def window_days(window: str) -> list[str]:
    """返回观察窗内周一到周日的日期字符串。"""
    monday = datetime.strptime(window, "%Y-%m-%d").date()
    return [(monday + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]


def _stable_seed(*parts: Any) -> int:
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


def _linear(seed: int, index: int) -> int:
    """确定性线性同余序列，避免全局 random 状态污染其他模块。"""
    return (seed * 1103515245 + 12345 + index * 2654435761) & 0x7FFFFFFF


def build_cohort_samples(
    cohort_name: str,
    window: str,
    group_size: int,
    version: str,
    *,
    material_kind: str = "trigger",
) -> dict[str, Any]:
    """为单个分群生成一份带版本与采样日的异常样本。

    material_kind:
      - trigger：原告警触发时采样（值班判定依据）
      - review ：复查时另取的补充材料
    """
    days = window_days(window)
    seed = _stable_seed(cohort_name, window, version, material_kind)
    base = group_size if group_size > 0 else 100

    daily: list[dict[str, Any]] = []
    total = 0
    for index, day in enumerate(days):
        # 围绕群组大小做确定性波动；复查材料走另一条序列，天然与触发样本不同。
        fluctuate = _linear(seed, index) % 21 - 10  # -10 .. +10
        sampled = max(1, int(base + fluctuate * base / 100.0))
        total += sampled
        daily.append({
            "date": day,
            "weekday": WEEKDAY_LABELS[index],
            "sampled_count": sampled,
        })

    monday_count = daily[0]["sampled_count"]
    return {
        "cohort_name": cohort_name,
        "window": window,
        "version": version,
        "material_kind": material_kind,
        "group_size": group_size,
        "sample_size": len(daily),
        "total_sampled": total,
        "avg_sampled": round(total / len(daily), 2),
        "monday_sampled": monday_count,
        "daily": daily,
    }


def diff_percentage(sample_a: dict[str, Any], sample_b: dict[str, Any]) -> float:
    """按两份样本的当班采样量复算分群差值百分比。

    与分群对比分析既有口径保持一致：
    |A - B| / (A + B) * 100，分母兜底为 1。
    """
    size_a = float(sample_a.get("total_sampled", 0))
    size_b = float(sample_b.get("total_sampled", 0))
    return round(abs(size_a - size_b) / max(1.0, size_a + size_b) * 100, 2)


def monday_diff(sample_a: dict[str, Any], sample_b: dict[str, Any]) -> float:
    """仅取周一采样点复算差值，供复查结论核对。"""
    size_a = float(sample_a.get("monday_sampled", 0))
    size_b = float(sample_b.get("monday_sampled", 0))
    return round(abs(size_a - size_b) / max(1.0, size_a + size_b) * 100, 2)
