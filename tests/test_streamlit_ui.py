"""使用Streamlit官方AppTest执行实际页面交互，不触碰外部API。"""

import unittest
from unittest import mock
from pathlib import Path

try:
    from streamlit.testing.v1 import AppTest
except ImportError:
    AppTest = None


APP = Path(__file__).resolve().parents[1] / "app.py"


@unittest.skipUnless(AppTest is not None, "需要先安装requirements.txt中的streamlit")
class TestStreamlitUI(unittest.TestCase):
    def test_startup_renders_without_exception(self):
        app = AppTest.from_file(str(APP), default_timeout=15).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.title[0].value, "中车四方 · 管理会计决策辅助智能体")

    def test_presentation_revision_preserves_three_modes(self):
        app = AppTest.from_file(str(APP), default_timeout=15).run()
        self.assertFalse(app.exception)
        self.assertEqual([tab.label for tab in app.tabs], [
            "🤖 智能体模式", "📐 计算器模式", "🧪 开发者模式",
        ])
        displayed = "\n".join(str(element.value) for element in app.markdown)
        for unwanted in (
            "标准教学参数已冻结，不会因网页交互被写回JSON",
            "工具没有企业内部台账，输出不得作为企业真实生产决策",
            "自然语言交互会调用真实千问API及白名单Python工具",
            "此工作台始终使用随附的v2.2原版内核",
            "企业资料边界",
        ):
            self.assertNotIn(unwanted, displayed)

    def test_fact_lookup_capability_survives_page_removal(self):
        from agent_core import lookup_handoff_facts

        result = lookup_handoff_facts("中车四方2023年ABC公开报道")
        self.assertIsInstance(result, dict)
        self.assertEqual(result["status"], "matched")
        self.assertGreater(len(result["matched"]), 0)

    def test_example_placeholders_do_not_count_as_configured_api(self):
        with mock.patch.dict("os.environ", {
            "DASHSCOPE_API_KEY": "请替换为你的真实百炼API密钥",
            "DASHSCOPE_BASE_URL": "https://<你的WorkspaceId>.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        }):
            app = AppTest.from_file(str(APP), default_timeout=15).run()
        self.assertFalse(app.exception)
        self.assertTrue(any("尚未配置千问API" in item.value for item in app.info))

    def test_t0_button_shows_frozen_amounts(self):
        app = AppTest.from_file(str(APP), default_timeout=15).run()
        app.button[0].click().run()
        self.assertFalse(app.exception)
        values = {metric.label: metric.value for metric in app.metric}
        self.assertEqual(values["X分摊"], "¥22,000.00")
        self.assertEqual(values["Y分摊"], "¥78,000.00")

    def test_y_price_changes_scenario_optimally(self):
        app = AppTest.from_file(str(APP), default_timeout=15).run()
        # Streamlit表单中的控件修改与提交应在同一次测试事件中执行。
        app.number_input[2].set_value(1700.0)
        app.button[1].click().run()
        self.assertFalse(app.exception)
        values = {metric.label: metric.value for metric in app.metric}
        self.assertEqual(values["X最优件数"], "99")
        self.assertEqual(values["Y最优件数"], "68")
        self.assertEqual(values["最优总边际贡献"], "¥111,650.00")

    def test_t0_result_survives_a_different_form_submission(self):
        app = AppTest.from_file(str(APP), default_timeout=15).run()
        app.button[0].click().run()
        self.assertFalse(app.exception)
        app.number_input[2].set_value(1700.0)
        app.button[1].click().run()
        values = {metric.label: metric.value for metric in app.metric}
        self.assertEqual(values["X分摊"], "¥22,000.00")
        self.assertEqual(values["Y最优件数"], "68")

    def test_standard_scenarios_explicit_recheck(self):
        app = AppTest.from_file(str(APP), default_timeout=15).run()
        app.button[2].click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any("逐一与约定件数" in item.value for item in app.success))

    def test_too_many_integer_candidates_display_validation_error(self):
        app = AppTest.from_file(str(APP), default_timeout=15).run()
        app.number_input[3].set_value(1000)
        app.number_input[4].set_value(1000)
        app.button[1].click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any("安全上限" in item.value for item in app.error))

    def test_bad_new_submission_clears_previous_valid_result(self):
        app = AppTest.from_file(str(APP), default_timeout=15).run()
        app.number_input[2].set_value(1700.0)
        app.button[1].click().run()
        self.assertEqual(next(m.value for m in app.metric if m.label == "X最优件数"), "99")
        app.number_input[3].set_value(1000)
        app.number_input[4].set_value(1000)
        app.button[1].click().run()
        self.assertTrue(any("安全上限" in item.value for item in app.error))
        self.assertFalse(any(m.label == "X最优件数" for m in app.metric),
                         "旧结果不能和新提交的报错同时展示")

    def test_all_seven_existing_scenario_fields_have_form_controls(self):
        app = AppTest.from_file(str(APP), default_timeout=15).run()
        labels = {field.label for field in app.number_input}
        self.assertIn("X最低生产数量（件）", labels)
        self.assertIn("Y最低生产数量（件）", labels)
        self.assertIn("X单件瓶颈加工工时（小时/件）", labels)
        self.assertIn("Y单件瓶颈加工工时（小时/件）", labels)
        self.assertIn("本情景新增固定相关支出总额（元，非每件）", labels)

    def test_extra_fixed_outlay_form_changes_residual_only(self):
        app = AppTest.from_file(str(APP), default_timeout=15).run()
        inputs = {field.label: field for field in app.number_input}
        inputs["本情景新增固定相关支出总额（元，非每件）"].set_value(2000.0)
        app.button[1].click().run()
        self.assertFalse(app.exception)
        values = {metric.label: metric.value for metric in app.metric}
        self.assertEqual(values["X最优件数"], "75")
        self.assertEqual(values["Y最优件数"], "100")
        self.assertEqual(values["最优总边际贡献"], "¥131,250.00")
        self.assertTrue(any("29,250.00" in str(item.value) for item in app.markdown))


if __name__ == "__main__":
    unittest.main()
