"""场景模板配置快照：发布冻结与实例化读取的唯一来源。

约定：
- 发布结果（配置快照）是预设指标、推荐图表、默认参数的唯一冻结来源；
- 编辑页里的可编辑字段只影响下一次发布，不参与已发布版本的实例化；
- 模板描述只作为历史附件，永远不进入配置快照；
- 快照一旦写入就不再修改，新发布只会追加新版本。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import threading
from typing import Any, Mapping

# 参与实例化的配置字段。顺序即对外展示顺序，也是快照的字段白名单。
SNAPSHOT_FIELDS = ("预设指标", "推荐图表", "默认参数")

# 编辑页可修改的字段；模板描述只作历史附件，不进入快照。
EDITABLE_FIELDS = ("模板名称", "适用场景", *SNAPSHOT_FIELDS, "模板描述")

# 历史附件字段：可以随编辑页修改，但不会被实例化读取。
ATTACHMENT_FIELDS = ("模板描述",)

VERSION_MODULE = "scene_version"
READ_MODULE = "scene_read"


class SceneSnapshotError(ValueError):
    """配置快照无法生成或读取时抛出，调用方据此保留既有版本。"""


def now_iso() -> str:
    """返回带时区的 ISO 时间串，供快照生成时刻/读取时刻使用。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def missing_snapshot_fields(source: Mapping[str, Any]) -> list[str]:
    """列出配置快照缺少的字段；全部就绪才允许发布。"""
    return [
        field
        for field in SNAPSHOT_FIELDS
        if not str(source.get(field) or "").strip()
    ]


def build_config_snapshot(
    source: Mapping[str, Any],
    *,
    template_id: int,
    template_code: str,
    version: int,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """从发布瞬间的可编辑字段生成一份完整快照。

    先在内存里构造完整结构并校验，再交给持久化层写入；任何字段缺失都会
    在写版本表之前失败，因此不会留下半份配置。模板描述不在白名单里，
    不会被带进快照。
    """
    missing = missing_snapshot_fields(source)
    if missing:
        raise SceneSnapshotError(f"缺少配置字段：{'、'.join(missing)}")

    snapshot: dict[str, Any] = {
        "template_id": int(template_id),
        "模板编号": str(template_code),
        "版本号": int(version),
    }
    for field in SNAPSHOT_FIELDS:
        snapshot[field] = str(source.get(field))
    snapshot["快照生成时刻"] = generated_at or now_iso()
    return snapshot


def snapshot_config(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """只取快照中的配置字段与版本元数据，供实例化/编辑页共用读取。"""
    view: dict[str, Any] = {
        "模板编号": snapshot.get("模板编号"),
        "版本号": int(snapshot.get("版本号", 0)),
        "快照生成时刻": snapshot.get("快照生成时刻"),
    }
    for field in SNAPSHOT_FIELDS:
        view[field] = snapshot.get(field)
    return view


class SceneSnapshotRepository:
    """发布快照的唯一读写入口：所有发布与实例化都必须走这里。"""

    def __init__(self, store: Any) -> None:
        self._store = store
        self._lock = threading.RLock()

    # ---- 读 ----
    def versions(self, template_id: int) -> list[dict[str, Any]]:
        """按版本号升序返回某模板的全部冻结快照（只读副本）。"""
        with self._lock:
            rows = [
                row
                for row in self._store.rows(VERSION_MODULE)
                if int(row.get("template_id", 0)) == int(template_id)
            ]
        rows.sort(key=lambda row: int(row.get("版本号", 0)))
        return deepcopy(rows)

    def latest(self, template_id: int) -> dict[str, Any] | None:
        """返回最新发布版本；没有发布过时返回 None。"""
        rows = self.versions(template_id)
        return rows[-1] if rows else None

    def get(self, template_id: int, version: int) -> dict[str, Any] | None:
        """按版本号精确读取，绝不回退到可编辑字段。"""
        with self._lock:
            for row in self._store.rows(VERSION_MODULE):
                if (
                    int(row.get("template_id", 0)) == int(template_id)
                    and int(row.get("版本号", 0)) == int(version)
                ):
                    return deepcopy(row)
        return None

    # ---- 写 ----
    def publish(self, template: Mapping[str, Any]) -> dict[str, Any]:
        """发布一次配置，追加一个新版本并返回冻结快照。

        完整快照在写任何数据之前构造完成；写入过程中任何一步失败都会回滚
        已追加的版本行，模板仍指向原版本，不会留下半份配置。
        """
        with self._lock:
            template_id = int(template.get("id", 0))
            template_code = str(template.get("模板编号") or "")
            rows = self._store.rows(VERSION_MODULE)
            next_version = max(
                (int(row.get("版本号", 0)) for row in rows
                 if int(row.get("template_id", 0)) == template_id),
                default=0,
            ) + 1

            snapshot = build_config_snapshot(
                template,
                template_id=template_id,
                template_code=template_code,
                version=next_version,
            )
            snapshot["id"] = max((int(row.get("id", 0)) for row in rows), default=0) + 1

            appended = False
            try:
                rows.append(snapshot)
                appended = True
                template["当前版本号"] = next_version
                template["当前快照生成时刻"] = snapshot["快照生成时刻"]
            except Exception:
                if appended:
                    rows.remove(snapshot)
                raise
            return deepcopy(snapshot)

    def record_read(
        self,
        snapshot: Mapping[str, Any],
        *,
        consumer: str,
    ) -> dict[str, Any]:
        """记录下游读取：快照生成时刻与版本号随读取记录一起落库。"""
        with self._lock:
            rows = self._store.rows(READ_MODULE)
            record = {
                "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
                "模板编号": snapshot.get("模板编号"),
                "template_id": int(snapshot.get("template_id", 0)),
                "版本号": int(snapshot.get("版本号", 0)),
                "快照生成时刻": snapshot.get("快照生成时刻"),
                "读取时刻": now_iso(),
                "读取入口": consumer,
            }
            rows.append(record)
            return dict(record)

    def reads(self, template_id: int | None = None) -> list[dict[str, Any]]:
        """返回下游读取记录；指定模板时只返回该模板的。"""
        with self._lock:
            rows = self._store.rows(READ_MODULE)
            if template_id is None:
                return deepcopy(rows)
            return deepcopy(
                [row for row in rows if int(row.get("template_id", 0)) == int(template_id)]
            )
