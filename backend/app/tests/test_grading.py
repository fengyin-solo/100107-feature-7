"""路面自动分档的端到端测试：只依赖标准库，运行方式

    cd backend && python3 -m unittest app.tests.test_grading -v
"""
from __future__ import annotations

import unittest

from app.services.grading_engine import verify_fingerprint
from app.services.grading_rules import registry
from app.services.pavement import PavementService
from app.store import store


class GradingTest(unittest.TestCase):
    def setUp(self) -> None:
        # 每个用例拿到首版规则与一份重跑首评的干净内存仓库
        registry._rules = registry._rules[:1]
        store.reset()
        self.svc = PavementService()

    def test_seed_grades(self) -> None:
        expected = {
            "PAVE-2026-001": "优",
            "PAVE-2026-002": "良",
            "PAVE-2026-003": "中",
            "PAVE-2026-004": "差",  # PCI 84.2 > 80 一票否决
            "PAVE-2026-005": "差",  # SFC 0.22 < 0.30 且给出偏离量
            "PAVE-2026-006": None,  # IRI 填了「未检测」
            "PAVE-2026-007": "中",
        }
        items, _ = self.svc.list_entries(size=50)
        for item in items:
            self.assertEqual(item["分档"], expected[item["评价编号"]], item["评价编号"])

    def test_pci_veto_clause(self) -> None:
        snap = self.svc.get_history(4)[-1]
        self.assertEqual(snap["grade"], "差")
        clause = next(i for i in snap["indicators"] if i["指标"] == "路面损坏指数")
        self.assertIn("超过一票否决线 80", clause["命中条款"])

    def test_sfc_deviation_text(self) -> None:
        snap = self.svc.get_history(5)[-1]
        sfc = next(i for i in snap["indicators"] if i["指标"] == "抗滑系数")
        self.assertIn("低 0.08", sfc["命中条款"])
        self.assertIn("26.7%", sfc["命中条款"])

    def test_same_road_distinct_sections(self) -> None:
        items, total = self.svc.list_entries(road="中山大道")
        self.assertEqual(total, 2)
        self.assertEqual({i["分档"] for i in items}, {"优", "良"})
        # 同名 + 同路段不允许重复登记
        entry, errors = self.svc.create_entry({
            "评价编号": "PAVE-DUP", "道路名称": "中山大道",
            "评价路段": "K0+000-K1+200（东段）",
            "路面损坏指数": 10, "平整度指数": 2, "抗滑系数": 0.6,
        })
        self.assertIsNone(entry)
        self.assertTrue(errors)

    def test_missing_and_bad_indicators_no_grade(self) -> None:
        items, total = self.svc.list_entries(ungraded_only=True)
        self.assertEqual(total, 1)
        self.assertTrue(any("平整度指数格式不对" in r for r in items[0]["未分档原因"]))

        entry, errors = self.svc.create_entry({
            "评价编号": "PAVE-T01", "道路名称": "测试路", "评价路段": "K0+000",
            "路面损坏指数": "高", "平整度指数": 2.0, "抗滑系数": 1.8,
        })
        self.assertFalse(errors)
        view = self.svc.get_entry(entry["id"])
        self.assertIsNone(view["分档"])
        reasons = "；".join(view["未分档原因"])
        self.assertIn("路面损坏指数格式不对", reasons)
        self.assertIn("抗滑系数超出合法范围", reasons)

    def test_required_fields_validated(self) -> None:
        entry, errors = self.svc.create_entry({"评价编号": "X", "道路名称": "Y"})
        self.assertIsNone(entry)
        self.assertTrue(any("评价路段" in e for e in errors))

    def test_indicator_correction_keeps_history(self) -> None:
        first = self.svc.get_history(6)
        self.assertEqual(len(first), 1)
        self.assertIsNone(first[0]["grade"])
        self.svc.update_indicators(6, {"平整度指数": 2.6})
        history = self.svc.get_history(6)
        self.assertEqual(len(history), 2)
        self.assertIsNone(history[0]["grade"])
        self.assertEqual(history[1]["grade"], "良")
        self.assertTrue(all(verify_fingerprint(s) for s in history))

    def test_rule_change_and_reevaluate(self) -> None:
        self.assertEqual(self.svc.get_history(7)[-1]["grade"], "中")
        first_fp_4 = self.svc.get_history(4)[0]["fingerprint"]
        registry.publish({"pci_poor_veto": 70.0, "pci_fair": 70.0},
                         published_by="王工", note="否决线收紧到70")
        report = self.svc.reevaluate_all()
        self.assertEqual(report["total"], 7)
        self.assertEqual(report["changed"], 1)  # PCI=75 的记录由中变差
        self.assertEqual(self.svc.get_history(7)[-1]["grade"], "差")
        # 录入时那份依据不变
        hist4 = self.svc.get_history(4)
        self.assertEqual(hist4[0]["rule_version"], 1)
        self.assertEqual(hist4[-1]["rule_version"], 2)
        self.assertEqual(hist4[0]["fingerprint"], first_fp_4)

    def test_fingerprint_detects_tampering(self) -> None:
        snap = self.svc.get_history(4)[0]
        self.assertTrue(verify_fingerprint(snap))
        tampered = dict(snap)
        tampered["grade"] = "优"
        self.assertFalse(verify_fingerprint(tampered))

    def test_rule_threshold_monotonicity(self) -> None:
        with self.assertRaises(ValueError):
            registry.publish({"sfc_fair": 0.9}, published_by="x", note="非法")

    def test_deterministic_for_equivalent_inputs(self) -> None:
        from app.services.grading_engine import evaluate
        rule = registry.get(1)
        a = evaluate({"路面损坏指数": 35, "平整度指数": 4.2, "抗滑系数": 0.45}, rule)
        b = evaluate({"路面损坏指数": "35", "平整度指数": "4.2", "抗滑系数": "0.45"}, rule)
        self.assertEqual(a["fingerprint"], b["fingerprint"])
