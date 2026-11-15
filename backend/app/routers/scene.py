"""场景化配置模板接口：维护场景模板，覆盖编辑模板、发布模板、下线模板、归档模板。

另外提供配置快照与实例化入口：发布结果与实例化共用同一份快照实现。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult, SceneActionPayload
from app.services.scene import SceneService

router = APIRouter(prefix="/api/scene", tags=["场景化配置模板"])

service = SceneService()

LIST_FIELDS = ["模板编号", "模板名称", "适用场景", "预设指标", "推荐图表", "默认参数", "模板描述", "模板状态"]
STATUSES = ["草稿", "已发布", "已下线", "已归档"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按模板编号检索"),
    status: str | None = Query(default=None, description="草稿、已发布、已下线、已归档"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按模板编号与状态过滤场景化配置模板列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


# 静态路径必须排在 /{entry_id} 之前，否则会被当作模板 id 解析。
@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出场景化配置模板清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "scene", "total": total, "items": items}


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条场景模板，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="场景模板已登记", entry=entry)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条场景模板明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"场景模板 {entry_id} 不存在或已归档")
    return entry


@router.put("/{entry_id}", response_model=ActionResult)
def edit_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """编辑模板的可编辑字段；已发布快照不变，模板描述仅作历史附件。"""
    entry, message = service.edit_entry(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/{entry_id}/versions", response_model=dict)
def list_versions(entry_id: int) -> dict:
    """按发布顺序列出全部冻结快照；历史模板同样可查，不按当前位置过滤。"""
    entry, versions = service.list_versions(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"场景模板 {entry_id} 不存在或已归档")
    return {"entry_id": entry_id, "模板编号": entry.get("模板编号"), "versions": versions}


@router.get("/{entry_id}/versions/{version}", response_model=dict)
def get_version(entry_id: int, version: int) -> dict:
    """读取指定版本的发布快照；版本不存在时明确报错，不回落可编辑字段。"""
    snapshot, message = service.get_snapshot(entry_id, version)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=message)
    return snapshot


@router.get("/{entry_id}/instantiate", response_model=dict)
def instantiate_entry(entry_id: int, version: int | None = Query(default=None)) -> dict:
    """分析入口实例化：只读指定/最新发布快照，并返回快照生成时刻与版本号。"""
    result, message = service.instantiate(entry_id, version)
    if result is None:
        status_code = 404 if "不存在" in message else 409
        raise HTTPException(status_code=status_code, detail=message)
    return result


@router.get("/{entry_id}/reads", response_model=dict)
def list_reads(entry_id: int) -> dict:
    """列出该模板的下游读取记录，便于核对快照生成时刻与版本号。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"场景模板 {entry_id} 不存在或已归档")
    return {
        "entry_id": entry_id,
        "模板编号": entry.get("模板编号"),
        "items": service.list_reads(entry_id),
    }


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: SceneActionPayload) -> ActionResult:
    """对单条场景模板执行发布模板、下线模板、归档模板；不允许的动作会被拦下并说明原因。"""
    action = payload.resolve_action()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
