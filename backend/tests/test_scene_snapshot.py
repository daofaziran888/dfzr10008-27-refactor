"""场景模板发布快照与实例化的回归测试。

运行：cd backend && .venv/bin/python -m unittest discover -s tests
"""
from __future__ import annotations

import unittest

from app.services.scene import SceneService
from app.services.scene_snapshot import SceneSnapshotRepository
from app.store import Store


class SceneSnapshotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store()
        self.service = SceneService(self.store)
        self.scn3 = self.store.find("scene", 3)

    def test_published_snapshot_wins_over_edited_fields(self) -> None:
        """SCN-0003：编辑成毛利后，实例化仍读原快照。"""
        self.assertEqual(self.scn3["预设指标"], "毛利")

        result, message = self.service.instantiate(3)

        self.assertEqual(message, "")
        self.assertEqual(result["版本号"], 1)
        self.assertEqual(
            result["实例化参数"],
            {"预设指标": "销售额、客单价", "推荐图表": "柱状图", "默认参数": "最近 7 天"},
        )

    def test_new_publish_creates_new_version_using_edited_fields(self) -> None:
        entry, _ = self.service.edit_entry(
            3, {"预设指标": "毛利", "推荐图表": "饼图", "默认参数": "最近 30 天"}
        )
        self.assertIsNotNone(entry)

        entry, message = self.service.run_action(3, "发布模板")

        self.assertIn("第 2 版", message)
        self.assertEqual(entry["当前版本号"], 2)
        latest, _ = self.service.instantiate(3)
        self.assertEqual(latest["版本号"], 2)
        self.assertEqual(latest["实例化参数"]["预设指标"], "毛利")
        old, _ = self.service.instantiate(3, 1)
        self.assertEqual(old["实例化参数"]["预设指标"], "销售额、客单价")

    def test_description_is_attachment_only(self) -> None:
        self.service.edit_entry(3, {"模板描述": "编辑页的新描述"})
        result, _ = self.service.instantiate(3)
        self.assertNotIn("模板描述", result["实例化参数"])

    def test_read_record_carries_snapshot_time_and_version(self) -> None:
        result, _ = self.service.instantiate(3)
        record = result["读取记录"]
        self.assertEqual(record["版本号"], result["版本号"])
        self.assertEqual(record["快照生成时刻"], result["快照生成时刻"])
        self.assertEqual(record["读取入口"], "分析入口")

    def test_failed_publish_keeps_existing_version(self) -> None:
        versions_before = self.service.snapshots.versions(3)
        status_before = self.scn3["status"]
        self.scn3["默认参数"] = ""

        entry, message = self.service.run_action(3, "发布模板")

        self.assertIsNone(entry)
        self.assertIn("已保留原有版本", message)
        self.assertEqual(self.service.snapshots.versions(3), versions_before)
        self.assertEqual(self.scn3["status"], status_before)

    def test_interrupted_write_rolls_back_partial_snapshot(self) -> None:
        repository = SceneSnapshotRepository(self.store)
        rows = self.store.rows("scene_version")
        before = len(rows)

        class FailingTemplate(dict):
            def __setitem__(self, key, value):
                if key == "当前版本号":
                    raise RuntimeError("write interrupted")
                super().__setitem__(key, value)

        with self.assertRaises(RuntimeError):
            repository.publish(FailingTemplate(self.scn3))

        self.assertEqual(len(rows), before)

    def test_history_visible_in_original_order(self) -> None:
        codes = [row["模板编号"] for row in self.store.rows("scene")]
        self.assertEqual(codes, ["SCN-0001", "SCN-0002", "SCN-0003"])
        versions = self.service.list_versions(3)[1]
        self.assertEqual([row["版本号"] for row in versions], [1])

    def test_offline_order_preserved(self) -> None:
        self.assertIn("不能执行", self.service.run_action(1, "下线模板")[1])
        self.assertIn("不能执行", self.service.run_action(2, "归档模板")[1])
        self.assertEqual(self.service.run_action(2, "下线模板")[0]["status"], "已下线")
        self.assertEqual(self.service.run_action(2, "归档模板")[0]["status"], "已归档")

    def test_unpublished_template_cannot_instantiate(self) -> None:
        result, message = self.service.instantiate(1)
        self.assertIsNone(result)
        self.assertIn("尚未发布", message)

    def test_explicit_missing_version_does_not_fall_back(self) -> None:
        result, message = self.service.instantiate(3, 99)
        self.assertIsNone(result)
        self.assertIn("没有第 99 版", message)

    def test_snapshot_rows_are_immutable_copies(self) -> None:
        first = self.service.snapshots.get(3, 1)
        first["预设指标"] = "被外部篡改"
        self.assertEqual(self.service.snapshots.get(3, 1)["预设指标"], "销售额、客单价")


if __name__ == "__main__":
    unittest.main()
