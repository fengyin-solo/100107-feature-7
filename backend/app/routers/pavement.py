"""路面状况接口：登记评价、自动分档、规则换版、重评与复核追溯。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.pavement import PavementService, rule_book

router = APIRouter(prefix="/api/pavement", tags=["路面状况"])

service = PavementService()

LIST_FIELDS = ["评价编号", "道路名称", "评价路段", "路面损坏指数", "平整度指数", "车辙深度", "抗滑系数", "评价日期"]
# 分档结论由规则引擎自动给出，评价员不再手填；指标不满足出档条件时为「待评定」。
GRADES = ["优", "良", "中", "差", "待评定"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按评价编号 / 道路名称 / 评价路段检索"),
    status: str | None = Query(default=None, description="分档结论：优、良、中、差、待评定"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按评价编号、道路名称、评价路段与分档结论过滤；同一路名下不同路段各自成行。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    if status and status not in GRADES:
        raise HTTPException(status_code=400, detail=f"分档结论只支持：{'、'.join(GRADES[:-1])}、待评定")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/rules", response_model=dict)
def list_rules() -> dict[str, Any]:
    """列出所有分档规则版本及当前生效版本。"""
    return {"active_version": rule_book.active.version, "rules": service.list_rules()}


@router.post("/rules/release", response_model=ActionResult)
def release_rule(payload: EntryPayload) -> ActionResult:
    """固化一版新分档口径并立即生效，同时把既有路面状况记录重新评一遍。"""
    result, reasons = service.release_rule(payload.values)
    if reasons:
        return ActionResult(ok=False, message=f"新规则未生效：{'；'.join(reasons)}")
    return ActionResult(ok=True, message=f"规则 {result['规则']} 已生效，既有记录已重评", entry=result)


@router.post("/regrade", response_model=ActionResult)
def regrade_all() -> ActionResult:
    """用当前生效规则对全部既有路面状况记录重新评价（换版时已自动触发，也可手工补跑）。"""
    report = service.regrade_all(reason="手工触发全量重评")
    return ActionResult(ok=True, message="既有路面状况记录已按当前规则重新评价", entry=report)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出路面状况清单：返回当前数据下的全量评价及其分档依据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "pavement", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条路面评价明细（含分档依据快照与历史）；不存在时给出可读说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"路面评价 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条路面评价：身份字段缺失拒绝登记；指标缺失/格式错仍登记但不出档并说明原因。"""
    entry, problems = service.create_entry(payload.values)
    if entry is None:
        return ActionResult(ok=False, message="；".join(problems))
    if entry.get("status") == "待评定":
        return ActionResult(ok=True, message=f"路面评价已登记，但未生成分档：{entry.get('不出档原因')}", entry=entry)
    return ActionResult(ok=True, message=f"路面评价已登记，自动分档：{entry.get('分档结论')}", entry=entry)


@router.post("/{entry_id}/regrade", response_model=ActionResult)
def regrade_entry(entry_id: int) -> ActionResult:
    """对单条既有评价按当前生效规则重新评价，原依据快照保留在分档历史中。"""
    entry, message = service.regrade_entry(entry_id)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/{entry_id}/verify", response_model=ActionResult)
def verify_basis(entry_id: int) -> ActionResult:
    """复核入口：校验当前分档依据指纹是否与录入时那份完全一致。"""
    result, message = service.verify_basis(entry_id)
    if result is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message="复核完成", entry=result)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """提交评价（自动出档）、下达维修、记录处置；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
