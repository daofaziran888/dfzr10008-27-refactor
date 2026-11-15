"""场景化配置模板业务规则：状态流转、字段校验与筛选口径都收在这里。

发布与实例化共用 ``SceneSnapshotRepository``：
- 发布时把可编辑字段冻结成新版本快照，模板描述只作为历史附件；
- 实例化只读版本快照，从不读取模板行上的可编辑字段。
"""
from __future__ import annotations

from typing import Any

from app.services.scene_snapshot import (
    EDITABLE_FIELDS,
    SceneSnapshotError,
    SceneSnapshotRepository,
    snapshot_config,
)
from app.store import Store, store as default_store
import threading

MODULE = "scene"
REQUIRED_FIELDS = ["模板编号", "模板名称", "适用场景"]
STATUS_ORDER = ["草稿", "已发布", "已下线", "已归档"]
# 动作 -> 允许执行的当前状态；下线/归档顺序保持原样。
ACTION_RULES = {"下线模板": "已发布", "归档模板": "已下线"}
PUBLISH_ACTION = "发布模板"
NEGATIVE_ACTIONS = ["下线模板"]

# 发布动作允许的起始状态：草稿首次发布，已发布/已下线可编辑后发新版本。
PUBLISHABLE_STATUSES = ["草稿", "已发布", "已下线"]


class SceneService:
    def __init__(self, store: Store | None = None) -> None:
        self._store = store or default_store
        self.snapshots = SceneSnapshotRepository(self._store)
        self._lock = threading.RLock()

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("模板编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        # 保持模板原有位置：状态流转只改字段，不调整列表顺序。
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return self._store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = self._store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in EDITABLE_FIELDS:
            if field not in entry and values.get(field) is not None:
                entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["当前版本号"] = 0
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def edit_entry(
        self, entry_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        """编辑可编辑字段；已发布版本的快照不受影响，模板描述仅作历史附件。"""
        entry = self._store.find(MODULE, entry_id)
        if entry is None:
            return None, f"场景模板 {entry_id} 不存在或已归档"
        if entry.get("status") == STATUS_ORDER[-1]:
            return None, f"场景模板 {entry_id} 已归档，不能再编辑"

        updated = False
        for field in EDITABLE_FIELDS:
            if field not in values:
                continue
            entry[field] = values.get(field)
            updated = True
        if not updated:
            return None, "没有可编辑的字段，请至少提交模板名称、适用场景、预设指标、推荐图表、默认参数或模板描述"
        entry["pending"] = True
        return entry, "场景模板已保存，发布后才会生成新的配置快照"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        with self._lock:
            entry = self._store.find(MODULE, entry_id)
            if entry is None:
                return None, f"场景模板 {entry_id} 不存在或已归档"
            if action not in ACTION_RULES and action != PUBLISH_ACTION:
                return None, f"动作「{action}」不属于场景化配置模板可执行范围"

            if action == PUBLISH_ACTION:
                if entry.get("status") not in PUBLISHABLE_STATUSES:
                    return None, f"场景模板当前状态「{entry.get('status')}」，不能发布"
                try:
                    snapshot = self.snapshots.publish(entry)
                except SceneSnapshotError as error:
                    # 发布失败时模板保持原状态与原版本，不写入任何半份配置。
                    return None, f"场景模板发布失败：{error}，已保留原有版本"
                entry["status"] = "已发布"
                entry["pending"] = False
                entry["abnormal"] = False
                return entry, f"场景模板已发布为第 {snapshot['版本号']} 版"

            # 既有下线/归档顺序保持不变：已发布才能下线，已下线才能归档。
            expected_from = ACTION_RULES[action]
            if entry.get("status") != expected_from:
                return None, f"场景模板当前状态「{entry.get('status')}」，不能执行「{action}」"
            entry["status"] = "已下线" if action == "下线模板" else "已归档"
            entry["pending"] = entry["status"] != STATUS_ORDER[-1]
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            return entry, f"场景模板已{action}"

    # ---- 配置快照读取：发布结果与实例化共用同一份实现 ----

    def list_versions(self, entry_id: int) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """按发布顺序返回全部冻结版本（历史模板同样可查）。"""
        entry = self._store.find(MODULE, entry_id)
        if entry is None:
            return None, []
        return entry, self.snapshots.versions(entry_id)

    def get_snapshot(
        self, entry_id: int, version: int | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        """读取发布快照；默认最新版本，指定版本时精确读取且不回落。"""
        entry = self._store.find(MODULE, entry_id)
        if entry is None:
            return None, f"场景模板 {entry_id} 不存在或已归档"
        snapshot = (
            self.snapshots.get(entry_id, version)
            if version is not None
            else self.snapshots.latest(entry_id)
        )
        if snapshot is None:
            if version is not None:
                return None, f"场景模板 {entry_id} 没有第 {version} 版发布快照"
            return None, f"场景模板 {entry_id} 尚未发布，没有可用的配置快照"
        return snapshot, ""

    def instantiate(self, entry_id: int, version: int | None = None) -> tuple[dict[str, Any] | None, str]:
        """分析入口实例化：只读发布快照，并记录快照生成时刻与版本号。

        这里与发布使用同一份 ``snapshot_config`` 口径，预设指标、推荐图表、
        默认参数全部来自冻结快照，可编辑字段与模板描述不会参与。
        """
        snapshot, message = self.get_snapshot(entry_id, version)
        if snapshot is None:
            return None, message
        config = snapshot_config(snapshot)
        read_record = self.snapshots.record_read(snapshot, consumer="分析入口")
        return {
            "ok": True,
            "模板编号": config["模板编号"],
            "版本号": config["版本号"],
            "快照生成时刻": config["快照生成时刻"],
            "实例化参数": {
                "预设指标": config["预设指标"],
                "推荐图表": config["推荐图表"],
                "默认参数": config["默认参数"],
            },
            "读取记录": read_record,
        }, ""

    def list_reads(self, entry_id: int | None = None) -> list[dict[str, Any]]:
        entry = self._store.find(MODULE, entry_id) if entry_id is not None else None
        if entry_id is not None and entry is None:
            return []
        return self.snapshots.reads(entry_id)
