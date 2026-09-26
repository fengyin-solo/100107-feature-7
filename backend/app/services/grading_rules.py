"""路面状况自动分档规则（固化口径）。

分档结论统一为四档：优、良、中、差。判定使用三项指标：

- 路面损坏指数 PCI：0~100，数值越大损坏越重。PCI 超过 80 时一票否决，统一按「差」；
- 平整度指数 IRI（国际平整度指数，m/km）：数值越大越颠簸；
- 抗滑系数 SFC：横向力系数，数值越小越滑，低于下限必须给出具体偏离范围。

三项指标分别比档后取最差档（就低原则）；任一指标缺失或格式不对时不生成分档，
并在结论里说明具体原因。

规则改动只能「发布新版本」，不能覆盖旧版本：每条评定记录都会快照当时的规则版本号，
保证复核人看到的分档依据与录入时那份完全一致。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# 四档口径（注意：旧骨架里曾有「次」档，按需求固化为优/良/中/差四档）
GRADES = ["优", "良", "中", "差"]
GRADE_RANK = {grade: index for index, grade in enumerate(GRADES)}

# 参与自动分档的三项指标及其录入名
INDICATOR_FIELDS = {
    "pci": "路面损坏指数",
    "iri": "平整度指数",
    "sfc": "抗滑系数",
}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


@dataclass(frozen=True)
class GradingRule:
    """某一版本的分档规则。一经发布即为不可变对象。"""

    version: int
    published_at: str
    published_by: str
    note: str
    # PCI（损坏指数，越大越差）：各档上界，半开区间
    # 优: pci <= pci_excellent；良: <= pci_good；中: <= pci_fair；
    # 差: pci > pci_fair。pci_poor_veto 为一票否决线，超过直接差。
    pci_excellent: float
    pci_good: float
    pci_fair: float
    pci_poor_veto: float
    # IRI（m/km，越大越差）：各档上界
    iri_excellent: float
    iri_good: float
    iri_fair: float
    # 抗滑系数（越小越差）：各档下界
    sfc_excellent: float
    sfc_good: float
    sfc_fair: float
    # 指标合法量纲范围（格式校验用）
    pci_range: tuple[float, float] = (0.0, 100.0)
    iri_range: tuple[float, float] = (0.0, 20.0)
    sfc_range: tuple[float, float] = (0.0, 1.0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "published_at": self.published_at,
            "published_by": self.published_by,
            "note": self.note,
            "路面损坏指数": {
                "优": f"≤ {self.pci_excellent:g}",
                "良": f"({self.pci_excellent:g}, {self.pci_good:g}]",
                "中": f"({self.pci_good:g}, {self.pci_fair:g}]",
                "差": f"> {self.pci_fair:g}（超过 {self.pci_poor_veto:g} 一票否决）",
            },
            "平整度指数": {
                "优": f"≤ {self.iri_excellent:g}",
                "良": f"({self.iri_excellent:g}, {self.iri_good:g}]",
                "中": f"({self.iri_good:g}, {self.iri_fair:g}]",
                "差": f"> {self.iri_fair:g}",
            },
            "抗滑系数": {
                "优": f"≥ {self.sfc_excellent:g}",
                "良": f"[{self.sfc_good:g}, {self.sfc_excellent:g})",
                "中": f"[{self.sfc_fair:g}, {self.sfc_good:g})",
                "差": f"< {self.sfc_fair:g}",
            },
        }


# V1 为固化的初始口径。阈值参考城市道路路面评价常用区间，集中在此、可审阅、可调。
INITIAL_RULE = GradingRule(
    version=1,
    published_at="2026-09-01 09:00:00",
    published_by="系统初始化",
    note="首版固化口径：PCI>80 一票按差；抗滑低于下限给出具体偏离值；三指标就低取档。",
    pci_excellent=20.0,
    pci_good=40.0,
    pci_fair=80.0,
    pci_poor_veto=80.0,
    iri_excellent=3.0,
    iri_good=5.0,
    iri_fair=8.0,
    sfc_excellent=0.50,
    sfc_good=0.40,
    sfc_fair=0.30,
)


class RuleRegistry:
    """规则版本库：只追加、不改写，current 始终指向最新发布版本。"""

    def __init__(self, initial: GradingRule = INITIAL_RULE) -> None:
        self._rules: list[GradingRule] = [initial]

    def current(self) -> GradingRule:
        return self._rules[-1]

    def get(self, version: int) -> GradingRule | None:
        for rule in self._rules:
            if rule.version == version:
                return rule
        return None

    def history(self) -> list[dict[str, Any]]:
        return [rule.as_dict() for rule in reversed(self._rules)]

    def publish(self, values: dict[str, Any], *, published_by: str, note: str) -> GradingRule:
        """发布一版新规则。阈值必须满足单调关系且量纲范围合法，否则抛 ValueError。"""
        rule = self._build(self.current().version + 1, values, published_by=published_by, note=note)
        self._rules.append(rule)
        return rule

    def _build(self, version: int, values: dict[str, Any], *, published_by: str, note: str) -> GradingRule:
        base = self.current()
        keys = [
            "pci_excellent", "pci_good", "pci_fair", "pci_poor_veto",
            "iri_excellent", "iri_good", "iri_fair",
            "sfc_excellent", "sfc_good", "sfc_fair",
        ]
        picked: dict[str, float] = {}
        for key in keys:
            raw = values.get(key, getattr(base, key))
            picked[key] = _to_number(key, raw)

        if not picked["pci_excellent"] < picked["pci_good"] < picked["pci_fair"]:
            raise ValueError("路面损坏指数阈值需满足 优 < 良 < 中（上界）")
        if not picked["pci_poor_veto"] <= picked["pci_fair"]:
            raise ValueError("一票否决线不能高于中档上界")
        if not picked["iri_excellent"] < picked["iri_good"] < picked["iri_fair"]:
            raise ValueError("平整度指数阈值需满足 优 < 良 < 中（上界）")
        if not picked["sfc_fair"] < picked["sfc_good"] < picked["sfc_excellent"]:
            raise ValueError("抗滑系数阈值需满足 中 < 良 < 优（下界递增）")

        return GradingRule(
            version=version,
            published_at=_now(),
            published_by=published_by.strip() or "管理员",
            note=note.strip() or "未填写改动说明",
            **picked,
        )


def _to_number(key: str, raw: Any) -> float:
    if isinstance(raw, bool):
        raise ValueError(f"阈值 {key} 不能是布尔值")
    if isinstance(raw, (int, float)):
        value = float(raw)
    else:
        text = str(raw or "").strip()
        if not text:
            raise ValueError(f"阈值 {key} 为空")
        try:
            value = float(text)
        except ValueError as exc:
            raise ValueError(f"阈值 {key} 格式不对：{text!r} 不是数值") from exc
    if value < 0:
        raise ValueError(f"阈值 {key} 不能为负数")
    return value


# 进程内单例（与内存仓库一致；换数据库时这里改为持久化版本表）
registry = RuleRegistry()
