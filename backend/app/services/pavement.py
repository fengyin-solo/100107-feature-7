"""路面状况业务规则：登记、自动分档、依据留痕、规则改版重评。

与旧版的区别：分档结论（优/良/中/差）不再由评价员手填，统一由 grading_engine
按固化规则算出。每条评价编号下保存一条 append-only 的评定历史，最新评定用于列表
展示，录入当时那份快照永久保留，复核人凭指纹可核对一致性。

判定口径的区分粒度是「道路名称 + 评价路段」：同名道路下不同路段分别评定、分别留痕。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.grading_engine import apply_grading, verify_fingerprint
from app.services.grading_rules import GRADES, INDICATOR_FIELDS, registry
from app.store import store

MODULE = "pavement"
REQUIRED_FIELDS = ["评价编号", "道路名称", "评价路段"]
INDICATOR_NAMES = list(INDICATOR_FIELDS.values())
OPTIONAL_FIELDS = ["车辙深度", "评价日期"]
EDITABLE_FIELDS = REQUIRED_FIELDS + INDICATOR_NAMES + OPTIONAL_FIELDS


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


class PavementService:
    # ---------- 查询 ----------
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        grade: str | None = None,
        road: str | None = None,
        section: str | None = None,
        ungraded_only: bool = False,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("评价编号", ""))]
        if road:
            rows = [row for row in rows if road in str(row.get("道路名称", ""))]
        if section:
            rows = [row for row in rows if section in str(row.get("评价路段", ""))]
        if grade:
            rows = [row for row in rows if self.current_grade(row) == grade]
        if ungraded_only:
            rows = [row for row in rows if self.current_grade(row) is None]
        rows = sorted(rows, key=lambda row: str(row.get("评价编号", "")))
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._view(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return self._view(row) if row else None

    @staticmethod
    def current_snapshot(row: dict[str, Any]) -> dict[str, Any] | None:
        history = row.get("grading_history") or []
        return history[-1] if history else None

    def current_grade(self, row: dict[str, Any]) -> str | None:
        snapshot = self.current_snapshot(row)
        return snapshot.get("grade") if snapshot else None

    def _view(self, row: dict[str, Any]) -> dict[str, Any]:
        """列表/明细视图：展示最新分档，但历史依据原样保留在 grading_history 里。"""
        snapshot = self.current_snapshot(row)
        view = {key: row.get(key) for key in EDITABLE_FIELDS}
        view["id"] = row["id"]
        view["分档"] = snapshot.get("grade") if snapshot else None
        view["未分档原因"] = snapshot.get("reasons") if snapshot and not snapshot.get("grade") else []
        view["规则版本"] = snapshot.get("rule_version") if snapshot else None
        view["评定时间"] = snapshot.get("evaluated_at") if snapshot else None
        view["依据指纹"] = snapshot.get("fingerprint") if snapshot else None
        return view

    # ---------- 登记 / 修改 ----------
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [
            field for field in REQUIRED_FIELDS
            if not str(values.get(field) or "").strip()
        ]
        if missing:
            return None, [f"缺少必填字段：{name}" for name in missing]

        rows = store.rows(MODULE)
        code = str(values["评价编号"]).strip()
        if any(str(row.get("评价编号")) == code for row in rows):
            return None, [f"评价编号 {code} 已存在，不能重复登记"]
        road, section = str(values["道路名称"]).strip(), str(values["评价路段"]).strip()
        if any(
            str(row.get("道路名称")) == road and str(row.get("评价路段")) == section
            for row in rows
        ):
            return None, [f"道路「{road}」下评价路段「{section}」已有评价记录，请按不同路段分别登记"]

        entry: dict[str, Any] = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "grading_history": [],
            "created_at": _now(),
        }
        entry.update({field: values.get(field) for field in EDITABLE_FIELDS})
        rows.append(entry)
        self._grade_once(entry, trigger="登记评定")
        return self._view(entry), []

    def update_indicators(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"路面评价 {entry_id} 不存在或已归档"
        for field in INDICATOR_NAMES:
            if field in values:
                entry[field] = values[field]
        for field in OPTIONAL_FIELDS:
            if field in values and str(values[field] or "").strip():
                entry[field] = values[field]
        self._grade_once(entry, trigger="指标更正后重新评定")
        return self._view(entry), "指标已更新并按当前规则重新评定"

    # ---------- 评定与留痕 ----------
    def _grade_once(self, entry: dict[str, Any], *, trigger: str, rule_version: int | None = None) -> dict[str, Any]:
        rule = registry.get(rule_version) if rule_version else registry.current()
        if rule is None:
            raise ValueError(f"规则版本 {rule_version} 不存在")
        return apply_grading(entry, rule, trigger=trigger)

    def reevaluate_all(self, *, rule_version: int | None = None) -> dict[str, Any]:
        """规则改动后把既有路面状况记录全部重新评一遍。

        旧快照不删不改（追溯用），新快照追加到每条记录的 grading_history；
        指标缺失/格式不对的记录同样追加一条「未分档」结论并说明原因。
        """
        rule = registry.get(rule_version) if rule_version else registry.current()
        changed, unchanged, ungraded = 0, 0, 0
        for row in store.rows(MODULE):
            before = self.current_grade(row)
            self._grade_once(row, trigger=f"规则改版重评（v{rule.version}）", rule_version=rule.version)
            after = self.current_grade(row)
            if after is None:
                ungraded += 1
            if before != after:
                changed += 1
            else:
                unchanged += 1
        return {
            "rule_version": rule.version,
            "total": changed + unchanged,
            "changed": changed,
            "unchanged": unchanged,
            "ungraded": ungraded,
            "reevaluated_at": _now(),
        }

    # ---------- 复核 ----------
    def get_history(self, entry_id: int) -> list[dict[str, Any]] | None:
        row = store.find(MODULE, entry_id)
        if row is None:
            return None
        history: list[dict[str, Any]] = []
        for snapshot in row.get("grading_history") or []:
            copy = dict(snapshot)
            copy["依据一致"] = verify_fingerprint(copy)
            history.append(copy)
        return history

    def grade_choices(self) -> list[str]:
        return GRADES
