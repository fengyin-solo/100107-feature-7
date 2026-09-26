"""路面状况接口：登记路面评价、自动分档、分档依据留痕、规则改版与重评。

分档结论不由前端提交：优/良/中/差全部由服务端按固化规则计算。指标缺失或格式不对时
记录仍可登记，但不生成分档，并把原因随记录返回。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.grading_rules import registry
from app.services.pavement import PavementService

router = APIRouter(prefix="/api/pavement", tags=["路面状况"])

service = PavementService()

LIST_FIELDS = [
    "评价编号", "道路名称", "评价路段",
    "路面损坏指数", "平整度指数", "抗滑系数", "车辙深度", "评价日期",
]
GRADES = ["优", "良", "中", "差"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按评价编号检索"),
    grade: str | None = Query(default=None, description="自动分档：优、良、中、差"),
    road: str | None = Query(default=None, description="按道路名称模糊检索"),
    section: str | None = Query(default=None, description="按评价路段模糊检索"),
    ungraded_only: bool = Query(default=False, description="只看指标缺失/格式不对、未分档的记录"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按评价编号、道路、路段与自动分档过滤；同一路名下不同路段各自一条、各自分档。"""
    if grade and grade not in GRADES:
        raise HTTPException(status_code=400, detail=f"分档只支持：{'、'.join(GRADES)}")
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword, grade=grade, road=road, section=section,
        ungraded_only=ungraded_only, page=page, size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/grades")
def grade_meta() -> dict[str, Any]:
    """分档口径与当前生效规则版本，供前端展示判定标准。"""
    return {"grades": GRADES, "current_rule": registry.current().as_dict()}


@router.get("/rules")
def list_rules() -> dict[str, Any]:
    """规则版本历史（最新在前）。规则只追加不改写，旧版本永久可查。"""
    return {"current_version": registry.current().version, "rules": registry.history()}


@router.post("/rules/publish", response_model=ActionResult)
def publish_rule(payload: EntryPayload) -> ActionResult:
    """发布新版分档阈值。发布后既有记录不会静默改档，需要显式调用重评接口。"""
    values = payload.values or {}
    try:
        rule = registry.publish(
            values,
            published_by=str(values.get("published_by") or "管理员"),
            note=str(values.get("note") or ""),
        )
    except ValueError as exc:
        return ActionResult(ok=False, message=f"新规则未发布：{exc}")
    return ActionResult(ok=True, message=f"分档规则 v{rule.version} 已发布，请对既有记录执行重评", entry=rule.as_dict())


@router.post("/reevaluate", response_model=ActionResult)
def reevaluate_all(payload: EntryPayload | None = None) -> ActionResult:
    """按最新（或指定版本）规则把既有路面状况记录全部重新评一遍，返回档位变化统计。"""
    rule_version = None
    if payload and payload.values:
        raw = payload.values.get("rule_version")
        if raw not in (None, ""):
            try:
                rule_version = int(raw)
            except (TypeError, ValueError):
                return ActionResult(ok=False, message=f"规则版本号格式不对：{raw!r}")
    if rule_version is not None and registry.get(rule_version) is None:
        return ActionResult(ok=False, message=f"规则版本 {rule_version} 不存在")
    report = service.reevaluate_all(rule_version=rule_version)
    return ActionResult(
        ok=True,
        message=(
            f"已按规则 v{report['rule_version']} 重评 {report['total']} 条："
            f"档位变化 {report['changed']} 条、不变 {report['unchanged']} 条、"
            f"未分档 {report['ungraded']} 条"
        ),
        entry=report,
    )


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出路面状况清单：含每条记录的最新自动分档与所依据的规则版本。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "pavement", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条路面评价明细（含最新分档与未分档原因）；不存在给出可读说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"路面评价 {entry_id} 不存在或已归档")
    return entry


@router.get("/{entry_id}/grading-history")
def get_grading_history(entry_id: int) -> dict[str, Any]:
    """分档依据追溯：该评价编号下每一次评定的完整快照，复核人据此核对录入时那份依据。"""
    history = service.get_history(entry_id)
    if history is None:
        raise HTTPException(status_code=404, detail=f"路面评价 {entry_id} 不存在或已归档")
    return {"评价编号": entry_id, "count": len(history), "history": history}


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记路面评价并立即按当前规则自动分档。

    必填三项（评价编号、道路名称、评价路段）缺失会被拦下；三项指标缺失或格式不对时
    不阻断登记，但不生成分档，记录上会带具体原因。
    """
    entry, errors = service.create_entry(payload.values)
    if errors:
        return ActionResult(ok=False, message="；".join(errors))
    if entry and entry.get("未分档原因"):
        return ActionResult(
            ok=True,
            message=f"路面评价 {entry['评价编号']} 已登记，但未分档：{'；'.join(entry['未分档原因'])}",
            entry=entry,
        )
    return ActionResult(ok=True, message=f"路面评价已登记，自动分档：{entry['分档']}（规则 v{entry['规则版本']}）", entry=entry)


@router.post("/{entry_id}/indicators", response_model=ActionResult)
def update_indicators(entry_id: int, payload: EntryPayload) -> ActionResult:
    """更正指标后重新评定；历史依据保留，新依据追加，规则版本一并固化。"""
    entry, message = service.update_indicators(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    if entry.get("未分档原因"):
        return ActionResult(ok=True, message=f"{message}，但仍未分档：{'；'.join(entry['未分档原因'])}", entry=entry)
    return ActionResult(ok=True, message=f"{message}，当前分档：{entry['分档']}", entry=entry)
