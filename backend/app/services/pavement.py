"""路面状况业务规则：自动分档、不可变依据快照、换版重评都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.pavement_grading import (
    INDICATORS,
    RuleBook,
    evaluate,
    now_stamp,
    parse_thresholds,
    verify_snapshot,
)
from app.store import store

MODULE = "pavement"
REQUIRED_FIELDS = ["评价编号", "道路名称", "评价路段"]
PENDING_GRADE = "待评定"

WORKFLOW_ACTIONS = {"下达维修", "记录处置"}

# 进程级规则台账：默认规则随启动固化；换版后既有记录统一重评。
rule_book = RuleBook()
_BOOTSTRAP_DONE = False


def _bootstrap() -> None:
    """启动后第一次访问时，用生效规则给既有记录补一次分档（只跑一次）。"""
    global _BOOTSTRAP_DONE
    if _BOOTSTRAP_DONE:
        return
    _BOOTSTRAP_DONE = True
    service = PavementService()
    service.regrade_all(rule=rule_book.active, reason="系统按固化规则初始化分档")


def _grade_signature(entry: dict[str, Any]) -> tuple[Any, ...]:
    """参与出档的原始录入值与规则版本；二者均未变则无需重复留档。"""
    return (
        rule_book.active.version,
        *(entry.get(field) for field in INDICATORS),
    )


class PavementService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        _bootstrap()
        rows = store.rows(MODULE)
        if keyword:
            needle = keyword.strip()

            def hit(row: dict[str, Any]) -> bool:
                return any(needle in str(row.get(field) or "") for field in ("评价编号", "道路名称", "评价路段"))

            rows = [row for row in rows if hit(row)]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        _bootstrap()
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        _bootstrap()
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        code = str(values.get("评价编号")).strip()
        if any(str(row.get("评价编号") or "").strip() == code for row in rows):
            return None, [f"评价编号 {code} 已存在，同一评价编号不得重复登记"]

        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in REQUIRED_FIELDS + ["路面损坏指数", "平整度指数", "车辙深度", "抗滑系数", "评价日期"]:
            entry[field] = values.get(field)
        entry["处置状态"] = "未下达"
        entry["分档历史"] = []
        entry["_grade_signature"] = None
        self._apply_grade(entry, rule_book.active)
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        _bootstrap()
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"路面评价 {entry_id} 不存在或已归档"
        if action == "提交评价":
            changed = self._apply_grade(entry, rule_book.active)
            if entry.get("status") == PENDING_GRADE:
                return entry, f"评价已提交，但指标不满足出档条件：{entry.get('不出档原因', '')}"
            return entry, ("评价已提交，分档已按固化规则重新计算" if changed else "评价已提交，分档依据与当前规则一致，未重复留档")
        if action in WORKFLOW_ACTIONS:
            target = {"下达维修": "维修中", "记录处置": "已处置"}[action]
            entry["处置状态"] = target
            entry["pending"] = target != "已处置"
            return entry, f"路面评价已{action}，分档结论保持 {entry.get('status')} 不变"
        return None, f"动作「{action}」不属于路面状况可执行范围"

    # ---- 分档核心 ----

    def _apply_grade(self, entry: dict[str, Any], rule: Any) -> bool:
        """按指定规则给单条记录出档；成功则追加一份不可变快照。

        返回是否真正新增了快照（原始录入值与规则版本都没变时不重复留档）。
        """
        snapshot, reasons = evaluate(
            {field: entry.get(field) for field in REQUIRED_FIELDS},
            {field: entry.get(field) for field in INDICATORS},
            rule,
        )
        history = entry.setdefault("分档历史", [])
        signature = _grade_signature(entry)
        if reasons:
            entry["status"] = PENDING_GRADE
            entry["分档结论"] = None
            entry["分档依据"] = None
            entry["不出档原因"] = "；".join(reasons)
            entry["pending"] = True
            entry["abnormal"] = False
            entry["_grade_signature"] = signature
            return False
        if entry.get("_grade_signature") == signature and entry.get("分档依据"):
            return False
        assert snapshot is not None
        history.append(snapshot)
        entry["status"] = snapshot["分档结论"]
        entry["分档结论"] = snapshot["分档结论"]
        entry["分档依据"] = snapshot
        entry["不出档原因"] = ""
        entry["规则版本"] = rule.version
        entry["pending"] = entry.get("处置状态", "未下达") != "已处置"
        entry["abnormal"] = bool(snapshot.get("偏离说明")) or snapshot["分档结论"] == "差"
        entry["_grade_signature"] = signature
        return True

    def regrade_entry(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """用当前生效规则对单条既有记录重新评价。"""
        _bootstrap()
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"路面评价 {entry_id} 不存在或已归档"
        rule = rule_book.active
        before = entry.get("分档结论")
        changed = self._apply_grade(entry, rule)
        if entry.get("status") == PENDING_GRADE:
            return entry, f"重新评价完成：指标不满足出档条件（{entry.get('不出档原因')}），原分档未保留为当前结论"
        if changed:
            if before is None:
                verdict = "指标补齐，已由待评定出档"
            else:
                verdict = f"结论由 {before} 调整为 {entry['分档结论']}" if before != entry["分档结论"] else "结论未变，已按新版本留档"
            return entry, f"已按规则 {rule.version} 重新评价：{verdict}"
        return entry, f"原始指标与规则 {rule.version} 下既有依据一致，无需重复留档"

    def regrade_all(self, *, rule: Any | None = None, reason: str = "规则变动后重评") -> dict[str, Any]:
        """规则改动后对既有路面状况记录逐条重评，返回留档统计。"""
        rule = rule or rule_book.active
        appended = grade_changed = unchanged = pending = 0
        for entry in store.rows(MODULE):
            before = entry.get("分档结论")
            changed = self._apply_grade(entry, rule)
            if entry.get("status") == PENDING_GRADE:
                pending += 1
            elif changed:
                appended += 1
                if before != entry["分档结论"]:
                    grade_changed += 1
            else:
                unchanged += 1
        return {
            "规则版本": rule.version,
            "原因": reason,
            "重评时间": now_stamp(),
            "重评记录数": len(store.rows(MODULE)),
            "新增依据份数": appended,
            "结论变化份数": grade_changed,
            "指标不满足出档条数": pending,
            "依据未变跳过条数": unchanged,
        }

    def release_rule(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """登记并生效新版本规则，随即对既有记录全部重评。"""
        _bootstrap()
        version = str(values.get("version") or "").strip()
        effective_from = str(values.get("effective_from") or now_stamp()[:10]).strip()
        cuts, reasons = parse_thresholds(values)
        if reasons:
            return None, reasons
        assert cuts is not None
        rule, error = rule_book.release(version, effective_from, cuts, str(values.get("note") or ""))
        if error:
            return None, [error]
        assert rule is not None
        report = self.regrade_all(rule=rule, reason=f"规则换版为 {version}")
        return {"规则": rule.version, "生效日期": rule.effective_from, "重评报告": report, "阈值": rule.thresholds()}, []

    def list_rules(self) -> list[dict[str, Any]]:
        return rule_book.list_rules()

    def verify_basis(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """复核入口：核对当前分档依据指纹是否与录入时那份完全一致。"""
        _bootstrap()
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"路面评价 {entry_id} 不存在或已归档"
        snapshot = entry.get("分档依据")
        if not isinstance(snapshot, dict):
            return None, f"当前没有可复核的分档依据（{entry.get('不出档原因') or '尚未出档'}）"
        return {
            "评价编号": snapshot.get("评价编号"),
            "规则版本": snapshot.get("规则版本"),
            "评定时间": snapshot.get("评定时间"),
            "快照指纹": snapshot.get("快照指纹"),
            "指纹一致": verify_snapshot(snapshot),
            "提示": "指纹一致表示复核人所见依据与录入时留档内容逐字相同",
        }, ""
