"""自动评定引擎：三项指标比档 → 四档结论，并生成可追溯的分档依据快照。

设计要点：
- 纯函数，无副作用：同一份指标 + 同一版规则，永远得到同一份依据（指纹可复算）；
- 指标缺失 / 格式不对 / 越界时 grade 为 None，不生成分档，reasons 逐条说明；
- PCI 超过一票否决线直接「差」，并在依据里标注命中否决；
- 抗滑系数低于下限时给出具体偏离量（低了多少、偏离下限百分之几）；
- 快照里固化规则版本号与规则文本，旧记录不会因规则升级而改变历史依据。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.services.grading_rules import (
    GRADES,
    GRADE_RANK,
    INDICATOR_FIELDS,
    GradingRule,
)

# 指标在录入字典里的字段名 → 内部键 / 量纲范围键 / 小数位
_SPECS = {
    "pci": {"label": "路面损坏指数", "range_attr": "pci_range", "digits": 1, "unit": ""},
    "iri": {"label": "平整度指数", "range_attr": "iri_range", "digits": 2, "unit": "m/km"},
    "sfc": {"label": "抗滑系数", "range_attr": "sfc_range", "digits": 2, "unit": ""},
}


@dataclass(frozen=True)
class Indicator:
    key: str
    label: str
    value: float
    grade: str
    clause: str
    note: str | None = None


def _parse_indicator(key: str, raw: Any, rule: GradingRule) -> tuple[Indicator | None, str | None]:
    spec = _SPECS[key]
    label = spec["label"]
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, f"{label}缺失，无法分档"
    if isinstance(raw, bool):
        return None, f"{label}格式不对：{raw!r} 不是数值"
    try:
        value = float(raw) if not isinstance(raw, str) else float(raw.strip())
    except (TypeError, ValueError):
        return None, f"{label}格式不对：{raw!r} 不是数值"
    low, high = getattr(rule, spec["range_attr"])
    if value != value or value < low or value > high:  # NaN 自查
        return None, f"{label}超出合法范围 [{low:g}, {high:g}]：录入值 {raw!r}"
    return Indicator(key=key, label=label, value=value, grade="", clause=""), None


def _grade_pci(value: float, rule: GradingRule) -> tuple[str, str, str | None]:
    note = None
    if value > rule.pci_poor_veto:
        grade = "差"
        clause = f"路面损坏指数 {value:g} 超过一票否决线 {rule.pci_poor_veto:g}，统一按差"
        note = "PCI 一票否决"
    elif value <= rule.pci_excellent:
        grade, clause = "优", f"路面损坏指数 {value:g} ≤ {rule.pci_excellent:g}"
    elif value <= rule.pci_good:
        grade, clause = "良", f"{rule.pci_excellent:g} < 路面损坏指数 {value:g} ≤ {rule.pci_good:g}"
    elif value <= rule.pci_fair:
        grade, clause = "中", f"{rule.pci_good:g} < 路面损坏指数 {value:g} ≤ {rule.pci_fair:g}"
    else:
        grade = "差"
        clause = f"路面损坏指数 {value:g} > {rule.pci_fair:g}"
    return grade, clause, note


def _grade_iri(value: float, rule: GradingRule) -> tuple[str, str]:
    if value <= rule.iri_excellent:
        return "优", f"平整度指数 {value:g} ≤ {rule.iri_excellent:g}"
    if value <= rule.iri_good:
        return "良", f"{rule.iri_excellent:g} < 平整度指数 {value:g} ≤ {rule.iri_good:g}"
    if value <= rule.iri_fair:
        return "中", f"{rule.iri_good:g} < 平整度指数 {value:g} ≤ {rule.iri_fair:g}"
    return "差", f"平整度指数 {value:g} > {rule.iri_fair:g}"


def _grade_sfc(value: float, rule: GradingRule) -> tuple[str, str, str | None]:
    note = None
    if value >= rule.sfc_excellent:
        grade, clause = "优", f"抗滑系数 {value:g} ≥ {rule.sfc_excellent:g}"
    elif value >= rule.sfc_good:
        grade, clause = "良", f"{rule.sfc_good:g} ≤ 抗滑系数 {value:g} < {rule.sfc_excellent:g}"
    elif value >= rule.sfc_fair:
        grade, clause = "中", f"{rule.sfc_fair:g} ≤ 抗滑系数 {value:g} < {rule.sfc_good:g}"
    else:
        grade = "差"
        gap = rule.sfc_fair - value
        percent = gap / rule.sfc_fair * 100
        clause = (
            f"抗滑系数 {value:g} 低于下限 {rule.sfc_fair:g}，"
            f"具体偏离：低 {gap:.2f}（偏离下限 {percent:.1f}%）"
        )
        note = f"低于抗滑下限 {gap:.2f}，偏离 {percent:.1f}%"
    return grade, clause, note


def evaluate(raw_values: dict[str, Any], rule: GradingRule) -> dict[str, Any]:
    """按指定版本规则评定。返回分档依据快照（dict，可直接落库/归档）。"""
    indicators: list[Indicator] = []
    reasons: list[str] = []

    for key in ("pci", "iri", "sfc"):
        parsed, error = _parse_indicator(key, raw_values.get(_SPECS[key]["label"]), rule)
        if error:
            reasons.append(error)
        else:
            assert parsed is not None
            indicators.append(parsed)

    if reasons:
        snapshot = _snapshot(
            rule=rule,
            grade=None,
            indicators=[],
            reasons=sorted(reasons),
            raw=raw_values,
        )
        return snapshot

    graded: list[Indicator] = []
    final_rank = 0
    veto = False
    for ind in indicators:
        if ind.key == "pci":
            grade, clause, note = _grade_pci(ind.value, rule)
            veto = note is not None
        elif ind.key == "iri":
            grade, clause = _grade_iri(ind.value, rule)
            note = None
        else:
            grade, clause, note = _grade_sfc(ind.value, rule)
        final_rank = max(final_rank, GRADE_RANK[grade])
        graded.append(Indicator(ind.key, ind.label, ind.value, grade, clause, note))

    final_grade = GRADES[final_rank]
    weakest = [ind.label for ind in graded if ind.grade == final_grade]
    if veto and final_grade == "差":
        summary = f"路面损坏指数超过 {rule.pci_poor_veto:g}，一票按差；其余指标不再上浮结论"
    else:
        summary = f"三项指标就低取档，控制指标：{'、'.join(weakest)}"

    return _snapshot(
        rule=rule,
        grade=final_grade,
        indicators=graded,
        reasons=[],
        raw=raw_values,
        summary=summary,
    )


def _snapshot(
    *,
    rule: GradingRule,
    grade: str | None,
    indicators: list[Indicator],
    reasons: list[str],
    raw: dict[str, Any],
    summary: str = "",
) -> dict[str, Any]:
    payload = {
        "grade": grade,
        "summary": summary,
        "indicators": [
            {
                "指标": ind.label,
                "录入值": ind.value,
                "单项档": ind.grade,
                "命中条款": ind.clause,
                **({"附注": ind.note} if ind.note else {}),
            }
            for ind in indicators
        ],
        "reasons": reasons,
        "rule_version": rule.version,
        "rule_note": rule.note,
        "rule_thresholds": {
            "pci": [rule.pci_excellent, rule.pci_good, rule.pci_fair, rule.pci_poor_veto],
            "iri": [rule.iri_excellent, rule.iri_good, rule.iri_fair],
            "sfc": [rule.sfc_excellent, rule.sfc_good, rule.sfc_fair],
        },
        "raw": {label: raw.get(label) for label in INDICATOR_FIELDS.values()},
    }
    payload["fingerprint"] = _fingerprint(payload)
    return payload


def _fingerprint(payload: dict[str, Any]) -> str:
    """依据指纹：对结论、解析后的指标值、条款、规则版本做哈希，供复核比对是否被改过。

    指纹锚定的是「判定要素」而非原始录入文本——数字 35 与字符串 "35" 判定等价，
    指纹也应相同；原始录入值另放在 raw 里用于展示。
    """
    canonical = json.dumps(
        {
            "grade": payload["grade"],
            "summary": payload["summary"],
            "indicators": [
                {k: ind.get(k) for k in ("指标", "录入值", "单项档", "命中条款", "附注")}
                for ind in payload["indicators"]
            ],
            "reasons": payload["reasons"],
            "rule_version": payload["rule_version"],
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def verify_fingerprint(snapshot: dict[str, Any]) -> bool:
    """复核侧校验：重算指纹，确认这份依据与归档时一致、未被篡改。"""
    stored = snapshot.get("fingerprint")
    if not stored:
        return False
    canonical = json.dumps(
        {
            "grade": snapshot.get("grade"),
            "summary": snapshot.get("summary"),
            "indicators": [
                {k: ind.get(k) for k in ("指标", "录入值", "单项档", "命中条款", "附注")}
                for ind in snapshot.get("indicators", [])
            ],
            "reasons": snapshot.get("reasons"),
            "rule_version": snapshot.get("rule_version"),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16] == stored


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def apply_grading(entry: dict[str, Any], rule: GradingRule, *, trigger: str) -> dict[str, Any]:
    """对一条评价记录执行评定并把不可变快照追加进 grading_history。

    服务层登记、指标更正、规则改版重评以及种子数据初始化都走这一个入口，
    保证「判定 → 快照 → 指纹 → 状态字段」的口径只有一份。
    """
    snapshot = evaluate(entry, rule)
    snapshot["evaluated_at"] = _now()
    snapshot["trigger"] = trigger
    snapshot["fingerprint"] = _fingerprint(snapshot)
    entry.setdefault("grading_history", []).append(snapshot)
    entry["status"] = snapshot["grade"] or "待评"
    entry["pending"] = snapshot["grade"] in (None, "中", "差")
    entry["abnormal"] = snapshot["grade"] == "差"
    return snapshot
