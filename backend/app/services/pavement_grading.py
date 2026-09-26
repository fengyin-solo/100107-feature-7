"""路面状况自动分档规则引擎。

判定口径全部固化在本模块，评价员不再手工填档：

- 参与比对的三项指标：路面损坏指数、平整度指数（IRI，越低越好）、抗滑系数（越高越好）。
- 三项指标各自先落入 优/良/中/差，路段结论按「就低原则」取最差的一项。
- 路面损坏指数超过中档上限（默认 80）时，无论其余指标如何，统一按差处理。
- 抗滑系数低于差档下限时，依据中必须写明具体偏离值与偏离比例。
- 任一指标缺失、格式不对或超出合理范围，不出分档结论，只逐条说明原因。
- 规则以版本（GradeRule）形式保存；每次出档生成不可变快照并附指纹，
  复核人据此核对「看到的依据」与录入时是否为同一份。
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

GRADES: tuple[str, ...] = ("优", "良", "中", "差")

INDICATORS = ("路面损坏指数", "平整度指数", "抗滑系数")

# 指标物理取值范围：能解析成数字但落不进该区间的，按脏数据处理，不出档。
VALUE_RANGES: dict[str, tuple[float, float]] = {
    "路面损坏指数": (0.0, 100.0),
    "平整度指数": (0.0, 20.0),
    "抗滑系数": (0.0, 1.0),
}

# 默认规则版本：项目首次上线即固化的判定口径。
DEFAULT_VERSION = "v2026.1"
DEFAULT_EFFECTIVE_FROM = "2026-01-01"
DEFAULT_PCI = (30.0, 50.0, 80.0)
DEFAULT_IRI = (3.0, 5.0, 8.0)
DEFAULT_SFC = (0.50, 0.45, 0.40)


@dataclass(frozen=True)
class GradeRule:
    """一个生效版本的分档阈值。

    pci / iri 为越低越好的三档上限（优上限、良上限、中上限），超过中上限为差；
    sfc 为越高越好的三档下限（优下限、良下限、中下限），低于中下限为差。
    """

    version: str
    effective_from: str
    pci: tuple[float, float, float]
    iri: tuple[float, float, float]
    sfc: tuple[float, float, float]
    note: str = ""

    def thresholds(self) -> dict[str, Any]:
        """写入快照的阈值明细，保证换版后旧依据仍能还原当时口径。"""
        return {
            "路面损坏指数": {"方向": "越低越好", "优上限": self.pci[0], "良上限": self.pci[1], "中上限": self.pci[2]},
            "平整度指数": {"方向": "越低越好", "优上限": self.iri[0], "良上限": self.iri[1], "中上限": self.iri[2]},
            "抗滑系数": {"方向": "越高越好", "优下限": self.sfc[0], "良下限": self.sfc[1], "中下限": self.sfc[2]},
            "硬性口径": f"路面损坏指数超过中上限（{_num(self.pci[2])}）时统一按差处理",
        }


def default_rule() -> GradeRule:
    return GradeRule(
        version=DEFAULT_VERSION,
        effective_from=DEFAULT_EFFECTIVE_FROM,
        pci=DEFAULT_PCI,
        iri=DEFAULT_IRI,
        sfc=DEFAULT_SFC,
        note="初始固化版本",
    )


def _num(value: float) -> str:
    """生成人话阈值文案：0.40 显示为 0.4，80.0 显示为 80。"""
    return f"{value:.3f}".rstrip("0").rstrip(".")


def now_stamp() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def parse_indicator(raw: Any, field: str) -> tuple[float | None, str | None]:
    """把录入值解析成指标数值；解析失败时返回可读原因而不是抛异常。"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, f"{field}缺失，无法参与分档"
    if isinstance(raw, bool):
        return None, f"{field}格式不正确（{raw!r} 不是数值），无法参与分档"
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None, f"{field}格式不正确（{raw} 不是数值），无法参与分档"
    if not math.isfinite(value):
        return None, f"{field}格式不正确（{raw} 不是有限数值），无法参与分档"
    low, high = VALUE_RANGES[field]
    if not low <= value <= high:
        return None, f"{field}取值 {_num(value)} 超出合理范围 [{_num(low)}, {_num(high)}]，无法参与分档"
    return value, None


def _low_better(value: float, cuts: tuple[float, float, float]) -> str:
    for index, cut in enumerate(cuts):
        if value <= cut + 1e-9:
            return GRADES[index]
    return GRADES[3]


def _high_better(value: float, cuts: tuple[float, float, float]) -> str:
    if value >= cuts[0] - 1e-9:
        return GRADES[0]
    if value >= cuts[1] - 1e-9:
        return GRADES[1]
    if value >= cuts[2] - 1e-9:
        return GRADES[2]
    return GRADES[3]


def evaluate(
    identity: dict[str, Any],
    raw_indicators: dict[str, Any],
    rule: GradeRule,
    *,
    graded_at: str | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """按指定规则版本对一条评价路段出档。

    身份字段取 评价编号 / 道路名称 / 评价路段：同名道路下不同路段各自独立出档。
    返回 (快照, 原因列表)；原因非空时快照为 None，表示本次不生成分档。
    """
    values: dict[str, float] = {}
    reasons: list[str] = []
    for field in INDICATORS:
        value, reason = parse_indicator(raw_indicators.get(field), field)
        if reason:
            reasons.append(reason)
        else:
            assert value is not None
            values[field] = value
    if reasons:
        return None, reasons

    pci = values["路面损坏指数"]
    iri = values["平整度指数"]
    sfc = values["抗滑系数"]
    bands: dict[str, str] = {
        "路面损坏指数": _low_better(pci, rule.pci),
        "平整度指数": _low_better(iri, rule.iri),
        "抗滑系数": _high_better(sfc, rule.sfc),
    }

    details: list[str] = []
    c0, c1, c2 = rule.pci
    if pci > c2:
        details.append(
            f"路面损坏指数 {_num(pci)} 超过中档上限 {_num(c2)}，按硬性口径统一定为差"
        )
    elif pci <= c0:
        details.append(f"路面损坏指数 {_num(pci)}：优（≤{_num(c0)}）")
    elif pci <= c1:
        details.append(f"路面损坏指数 {_num(pci)}：良（{_num(c0)}＜x≤{_num(c1)}）")
    else:
        details.append(f"路面损坏指数 {_num(pci)}：中（{_num(c1)}＜x≤{_num(c2)}）")

    i0, i1, i2 = rule.iri
    if iri <= i0:
        details.append(f"平整度指数 {_num(iri)}：优（≤{_num(i0)}）")
    elif iri <= i1:
        details.append(f"平整度指数 {_num(iri)}：良（{_num(i0)}＜x≤{_num(i1)}）")
    elif iri <= i2:
        details.append(f"平整度指数 {_num(iri)}：中（{_num(i1)}＜x≤{_num(i2)}）")
    else:
        details.append(f"平整度指数 {_num(iri)}：差（＞{_num(i2)}）")

    deviation = ""
    s0, s1, s2 = rule.sfc
    if sfc >= s0:
        details.append(f"抗滑系数 {_num(sfc)}：优（≥{_num(s0)}）")
    elif sfc >= s1:
        details.append(f"抗滑系数 {_num(sfc)}：良（{_num(s1)}≤x＜{_num(s0)}）")
    elif sfc >= s2:
        details.append(f"抗滑系数 {_num(sfc)}：中（{_num(s2)}≤x＜{_num(s1)}）")
    else:
        gap = s2 - sfc
        ratio = gap / s2 * 100 if s2 > 0 else 0.0
        deviation = (
            f"抗滑系数 {_num(sfc)} 低于下限 {_num(s2)}，"
            f"偏离 {_num(round(gap, 3))}（偏离比例 {ratio:.1f}%）"
        )
        details.append(f"{deviation}，定为差")

    conclusion = GRADES[max(GRADES.index(band) for band in bands.values())]
    details.append(f"三项指标就低取档，分档结论：{conclusion}")

    snapshot: dict[str, Any] = {
        "评价编号": str(identity.get("评价编号") or "").strip(),
        "道路名称": str(identity.get("道路名称") or "").strip(),
        "评价路段": str(identity.get("评价路段") or "").strip(),
        "规则版本": rule.version,
        "生效日期": rule.effective_from,
        "规则阈值": rule.thresholds(),
        "指标值": values,
        "指标分档": bands,
        "分档结论": conclusion,
        "依据明细": details,
        "偏离说明": deviation,
        "评定时间": graded_at or now_stamp(),
    }
    snapshot["快照指纹"] = fingerprint(snapshot)
    return snapshot, []


def fingerprint(snapshot: dict[str, Any]) -> str:
    """对除指纹外的整份依据做规范化哈希，作为「录入时那份依据」的凭据。"""
    core = {key: value for key, value in snapshot.items() if key != "快照指纹"}
    raw = json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_snapshot(snapshot: dict[str, Any]) -> bool:
    """复核时重新计算指纹，与录入时落库的指纹比对。"""
    stored = snapshot.get("快照指纹")
    if not isinstance(stored, str):
        return False
    return fingerprint(snapshot) == stored


def parse_thresholds(values: dict[str, Any]) -> tuple[dict[str, tuple[float, float, float]] | None, list[str]]:
    """校验新版本规则提交的 9 个阈值；顺序/区间不对时退回并说明。"""
    spec = {
        "pci": ("路面损坏指数", ["pci_you", "pci_liang", "pci_zhong"], True),
        "iri": ("平整度指数", ["iri_you", "iri_liang", "iri_zhong"], True),
        "sfc": ("抗滑系数", ["sfc_you", "sfc_liang", "sfc_zhong"], False),
    }
    # 同时兼容中文阈值名。
    aliases = {
        "pci_you": "损坏指数优上限", "pci_liang": "损坏指数良上限", "pci_zhong": "损坏指数中上限",
        "iri_you": "平整度优上限", "iri_liang": "平整度良上限", "iri_zhong": "平整度中上限",
        "sfc_you": "抗滑优下限", "sfc_liang": "抗滑良下限", "sfc_zhong": "抗滑中下限",
    }
    reasons: list[str] = []
    cuts: dict[str, tuple[float, float, float]] = {}
    for key, (label, names, ascending) in spec.items():
        nums: list[float] = []
        for name in names:
            raw = values.get(name, values.get(aliases[name]))
            try:
                num = float(str(raw).strip())
            except (TypeError, ValueError, AttributeError):
                reasons.append(f"{label}阈值「{aliases[name]}」缺失或不是数值")
                num = float("nan")
            nums.append(num)
        if reasons and any(math.isnan(num) for num in nums):
            continue
        ordered = all(nums[i] < nums[i + 1] for i in range(2)) if ascending else all(
            nums[i] > nums[i + 1] for i in range(2)
        )
        if not ordered:
            direction = "须严格递增（优＜良＜中）" if ascending else "须严格递减（优＞良＞中）"
            reasons.append(f"{label}三档阈值{direction}，当前为 {nums}")
        cuts[key] = (nums[0], nums[1], nums[2])
    if reasons:
        return None, reasons
    return cuts, []


class RuleBook:
    """版本化规则台账：只增不改，新版本生效后既有记录按新版本重评。"""

    def __init__(self) -> None:
        first = default_rule()
        self._rules: dict[str, GradeRule] = {first.version: first}
        self._active_version = first.version

    @property
    def active(self) -> GradeRule:
        return self._rules[self._active_version]

    def list_rules(self) -> list[dict[str, Any]]:
        return [
            {
                "规则版本": rule.version,
                "生效日期": rule.effective_from,
                "是否生效": rule.version == self._active_version,
                "损坏指数优上限": rule.pci[0],
                "损坏指数良上限": rule.pci[1],
                "损坏指数中上限": rule.pci[2],
                "平整度优上限": rule.iri[0],
                "平整度良上限": rule.iri[1],
                "平整度中上限": rule.iri[2],
                "抗滑优下限": rule.sfc[0],
                "抗滑良下限": rule.sfc[1],
                "抗滑中下限": rule.sfc[2],
                "备注": rule.note,
            }
            for rule in sorted(self._rules.values(), key=lambda item: item.effective_from)
        ]

    def release(
        self,
        version: str,
        effective_from: str,
        cuts: dict[str, tuple[float, float, float]],
        note: str = "",
    ) -> tuple[GradeRule | None, str]:
        version = version.strip()
        if not version:
            return None, "规则版本号不能为空"
        if version in self._rules:
            return None, f"规则版本 {version} 已存在，版本号不能复用"
        rule = GradeRule(
            version=version,
            effective_from=effective_from.strip(),
            pci=tuple(cuts["pci"]),  # type: ignore[arg-type]
            iri=tuple(cuts["iri"]),  # type: ignore[arg-type]
            sfc=tuple(cuts["sfc"]),  # type: ignore[arg-type]
            note=note.strip(),
        )
        self._rules[version] = rule
        self._active_version = version
        return rule, ""
