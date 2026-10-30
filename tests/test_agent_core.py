"""不访问外部API的智能体结构、工具分派、错误与伪模型联调。"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from agent_core import (  # noqa: E402
    AgentInputError, calculate_t0, calculate_t1, compare_t1, execute_tool,
    get_identity, load_frozen_data, lookup_handoff_facts, run_agent,
    verified_tool_answer, verify_interpreted_parameters,
)


class FakeCallCompletions:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.received = []

    def create(self, **kwargs):
        self.received.append(kwargs)
        item = next(self.outputs)
        return SimpleNamespace(choices=[SimpleNamespace(message=item)])


class FakeClient:
    def __init__(self, messages):
        self.completions = FakeCallCompletions(messages)
        self.chat = SimpleNamespace(completions=self.completions)


class TestFrozenDomain(unittest.TestCase):
    def test_original_baseline_contract(self):
        data = load_frozen_data()
        self.assertEqual(data["meta"]["version"], "handoff-v2.2-audited")

    def test_identity(self):
        identity = get_identity()
        self.assertEqual(identity["entity"], "中车青岛四方机车车辆股份有限公司")
        self.assertEqual(len(identity["engine_sha256"]), 64)

    def test_t0_abc(self):
        out = calculate_t0()["result"]
        self.assertEqual(out["allocated_support_cost"], {"X": 22000, "Y": 78000})
        self.assertEqual(out["unit_internal_residual"], {"X": 530, "Y": -30})
        self.assertEqual(out["simplified_period_residual"], 50000)

    def test_t1_three_frozen_scenarios(self):
        cases = [
            ({}, (75, 100, 131250, 31250)),
            ({"total_capacity_hours": 350}, (100, 100, 150000, 50000)),
            ({"price_per_unit": {"Y": 1700}}, (99, 68, 111650, 11650)),
        ]
        for override, expected in cases:
            with self.subTest(override=override):
                out = calculate_t1(override)["result"]
                self.assertEqual((out["x"], out["y"], out["total_contribution"],
                                  out["simplified_period_residual"]), expected)

    def test_effective_inputs_include_all_original_dynamic_fields(self):
        out = calculate_t1({"maximum_quantity": {"Y": 80},
                            "bottleneck_processing_hours_per_unit": {"X": 1.75},
                            "additional_fixed_outlay_for_scenario": 3000})
        inputs = out["effective_inputs"]
        self.assertEqual(inputs["maximum_quantity"], {"X": 100, "Y": 80})
        self.assertEqual(inputs["bottleneck_processing_hours_per_unit"], {"X": 1.75, "Y": 1.5})
        self.assertEqual(inputs["additional_fixed_outlay_for_scenario"], 3000)
        self.assertEqual(inputs["minimum_quantity"], {"X": 0, "Y": 0})

    def test_extra_fixed_outlay_affects_residual_not_contribution_or_mix(self):
        orig = calculate_t1()["result"]
        changed = calculate_t1({"additional_fixed_outlay_for_scenario": 2000})["result"]
        self.assertEqual((orig["x"], orig["y"]), (changed["x"], changed["y"]))
        self.assertEqual(orig["total_contribution"], changed["total_contribution"])
        self.assertEqual(orig["simplified_period_residual"] - changed["simplified_period_residual"], 2000)

    def test_compare_t1(self):
        out = compare_t1({"price_per_unit": {"Y": 1700}})
        self.assertEqual(out["difference"]["contribution_change"], -19600)
        self.assertEqual(out["difference"]["x_change"], 24)
        self.assertEqual(out["difference"]["y_change"], -32)

    def test_negative_capacity_rejected(self):
        with self.assertRaisesRegex(ValueError, "非负数"):
            calculate_t1({"total_capacity_hours": -1})

    def test_not_modelled_contract_rejected(self):
        with self.assertRaisesRegex(AgentInputError, "未建模"):
            calculate_t1({"signed_contract_minimum": 5})

    def test_unknown_products_rejected(self):
        with self.assertRaises(AgentInputError):
            calculate_t1({"price_per_unit": {"Z": 1700}})

    def test_model_never_modifies_baseline(self):
        original = get_identity()["data_sha256"]
        calculate_t1({"price_per_unit": {"Y": 1700}})
        self.assertEqual(original, get_identity()["data_sha256"])

    def test_zero_capacity_is_valid_with_zero_lower_bounds(self):
        out = calculate_t1({"total_capacity_hours": 0})["result"]
        self.assertEqual((out["x"], out["y"], out["total_contribution"]), (0, 0, 0))

    def test_cost_is_never_deducted_twice(self):
        out = calculate_t1()["result"]
        self.assertEqual(out["total_contribution"] - out["simplified_period_residual"], 100000)

    def test_invalid_non_integer_demand_rejected(self):
        with self.assertRaisesRegex(ValueError, "整数"):
            calculate_t1({"maximum_quantity": {"X": 10.5}})

    def test_any_extra_scenario_condition_rejected(self):
        with self.assertRaises(AgentInputError):
            calculate_t1({"minimum_delivery_contract": {"X": 1}})

    def test_only_whitelisted_tools(self):
        with self.assertRaises(AgentInputError):
            execute_tool("execute_shell", {"cmd": "anything"})

    def test_facts_known_and_unknown(self):
        matched = lookup_handoff_facts("作业成本 ABC")
        self.assertEqual(matched["status"], "matched")
        self.assertTrue(all(r["entity"] == "中车青岛四方机车车辆股份有限公司"
                            for r in matched["matched"]))
        missing = lookup_handoff_facts("真实产品Y亏损的订单具体编号")
        self.assertEqual(missing["status"], "no_matching_approved_handoff_excerpt")


class TestModelOrchestration(unittest.TestCase):
    @staticmethod
    def tool_call(name, args, call_id="call_1"):
        return SimpleNamespace(id=call_id, type="function",
                               function=SimpleNamespace(name=name, arguments=args))

    def test_numeric_question_cannot_be_answered_without_tool(self):
        client = FakeClient([SimpleNamespace(content="X100 Y66", tool_calls=None)])
        out = run_agent("T1 H300时最优方案？", api_key="", base_url="", client=client)
        self.assertEqual(out.status, "unverified_numeric_route")
        self.assertNotIn("X100 Y66", out.answer)
        self.assertIn("计算器模式", out.answer)

    def test_fact_question_cannot_be_answered_without_retrieval(self):
        client = FakeClient([SimpleNamespace(content="企业真实利润为100万元", tool_calls=None)])
        out = run_agent("中车四方实际实施了哪些管理会计做法？", api_key="", base_url="", client=client)
        self.assertEqual(out.status, "unverified_evidence_route")

    def test_model_calls_original_calculator(self):
        tool_call = SimpleNamespace(
            id="call_1", type="function",
            function=SimpleNamespace(name="compare_t1_scenarios",
                                     arguments='{"overrides":{"price_per_unit":{"Y":1700}}}'),
        )
        first = SimpleNamespace(content=None, tool_calls=[tool_call])
        second = SimpleNamespace(content="工具已经重新计算，请查看真实工具输出。", tool_calls=[])
        client = FakeClient([first, second])
        out = run_agent("Y售价降到1700时T1最优方案如何变化？",
                        api_key="", base_url="", client=client)
        self.assertEqual(out.status, "verified_tool_output")
        self.assertEqual(out.tool_records[0]["result"]["modified"]["result"]["x"], 99)
        self.assertEqual(out.tool_records[0]["result"]["modified"]["result"]["y"], 68)
        self.assertIn("111,650.00元", out.answer)
        self.assertEqual(len(client.completions.received), 2)

    def test_bad_dynamic_request_is_reported_without_execution(self):
        with self.assertRaises(AgentInputError):
            execute_tool("optimize_t1", {"overrides": {"extra_overtime_rate": 200}})

    def test_no_evidence_after_retrieval_cannot_be_fabricated(self):
        call = SimpleNamespace(
            id="call_evidence", type="function",
            function=SimpleNamespace(name="lookup_case_evidence",
                                     arguments='{"query":"真实产品Y亏损的订单具体编号"}'),
        )
        client = FakeClient([
            SimpleNamespace(content=None, tool_calls=[call]),
            SimpleNamespace(content="企业真实订单Y亏损500万元", tool_calls=[]),
        ])
        out = run_agent("中车四方真实产品Y亏损的订单编号是多少？",
                        api_key="", base_url="", client=client)
        self.assertEqual(out.status, "no_case_evidence")
        self.assertNotIn("500万元", out.answer)

    def test_wrong_numbers_in_model_final_message_discarded(self):
        call = self.tool_call("optimize_t1", '{"overrides":{"price_per_unit":{"Y":1700}}}')
        client = FakeClient([
            SimpleNamespace(content=None, tool_calls=[call]),
            SimpleNamespace(content="错误结论：X100、Y66，总贡献111300元。", tool_calls=[]),
        ])
        out = run_agent("T1把Y降价至1700元后最优产量是多少？",
                        api_key="", base_url="", client=client)
        self.assertEqual(out.status, "verified_tool_output")
        self.assertIn("X=99件，Y=68件", out.answer)
        self.assertNotIn("X100", out.answer)
        self.assertNotIn("111300", out.answer)

    def test_matched_evidence_does_not_authorize_new_profit_claims(self):
        call = self.tool_call("lookup_case_evidence", '{"query":"作业成本 ABC"}')
        client = FakeClient([
            SimpleNamespace(content=None, tool_calls=[call]),
            SimpleNamespace(content="中车四方2026真实利润为999亿元。", tool_calls=[]),
        ])
        out = run_agent("中车四方公开披露ABC实践包括什么？",
                        api_key="", base_url="", client=client)
        self.assertEqual(out.status, "verified_tool_output")
        self.assertIn("F-2023-ABC", out.answer)
        self.assertNotIn("999", out.answer)

    def test_t0_query_cannot_be_answered_by_t1_tool(self):
        call = self.tool_call("optimize_t1", '{}')
        client = FakeClient([
            SimpleNamespace(content=None, tool_calls=[call]),
            SimpleNamespace(content="T0已经计算完成", tool_calls=[]),
        ])
        out = run_agent("T0 ABC两阶段分摊是多少？", api_key="", base_url="", client=client)
        self.assertEqual(out.status, "unverified_numeric_route")

    def test_t1_query_cannot_be_answered_by_t0_tool(self):
        call = self.tool_call("calculate_t0_abc", '{}')
        client = FakeClient([
            SimpleNamespace(content=None, tool_calls=[call]),
            SimpleNamespace(content="T1最优就是ABC答案", tool_calls=[]),
        ])
        out = run_agent("T1 H300最优组合是多少？", api_key="", base_url="", client=client)
        self.assertEqual(out.status, "unverified_numeric_route")

    def test_price_request_without_word_t1_still_needs_t1_tool(self):
        call = self.tool_call("calculate_t0_abc", '{}')
        client = FakeClient([
            SimpleNamespace(content=None, tool_calls=[call]),
            SimpleNamespace(content="已根据售价优化。", tool_calls=[]),
        ])
        out = run_agent("Y售价降到1700后的最优组合是多少？", api_key="", base_url="", client=client)
        self.assertEqual(out.status, "unverified_numeric_route")

    def test_invalid_tool_request_stops_instead_of_being_ignored(self):
        call = self.tool_call("optimize_t1", '{"overrides":{"overtime_rate":100}}')
        client = FakeClient([SimpleNamespace(content=None, tool_calls=[call])])
        out = run_agent("T1 H300把加班费算进去怎样？",
                        api_key="", base_url="", client=client)
        self.assertEqual(out.status, "unmodelled_business_rule")
        self.assertEqual(len(client.completions.received), 0)

    def test_invalid_tool_schema_stops_even_if_model_promises_to_answer(self):
        call = self.tool_call("optimize_t1", '{"overrides":{"unmodelled":100}}')
        client = FakeClient([SimpleNamespace(content=None, tool_calls=[call])])
        out = run_agent("T1 H300考虑新条件后的最优组合？",
                        api_key="", base_url="", client=client)
        self.assertEqual(out.status, "tool_input_error")
        self.assertIn("未建模", out.answer)
        self.assertEqual(len(client.completions.received), 1)

    def test_model_response_truncated_is_rejected(self):
        class TruncatedClient:
            chat = SimpleNamespace(completions=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(
                    finish_reason="length", message=SimpleNamespace(content="partial", tool_calls=None))])))
        from agent_core import AgentRuntimeError
        with self.assertRaisesRegex(AgentRuntimeError, "长度上限"):
            run_agent("T1计算", api_key="", base_url="", client=TruncatedClient())

    def test_bad_tool_call_identifier_rejected(self):
        call = self.tool_call("optimize_t1", '{}', call_id="")
        client = FakeClient([SimpleNamespace(content=None, tool_calls=[call])])
        from agent_core import AgentRuntimeError
        with self.assertRaisesRegex(AgentRuntimeError, "不完整"):
            run_agent("T1 H300最优组合", api_key="", base_url="", client=client)

    def test_t1_minimum_quantity_remains_supported(self):
        # 最低数量是已冻结的动态字段，不等于临时新增"既签强制订单"业务规则。
        out = calculate_t1({"minimum_quantity": {"X": 10}})["result"]
        self.assertEqual(out["status"], "optimal")

    def test_explicit_no_inventory_assumption_does_not_block_existing_t1(self):
        call = self.tool_call("optimize_t1", '{}')
        client = FakeClient([
            SimpleNamespace(content=None, tool_calls=[call]),
            SimpleNamespace(content="已计算。", tool_calls=[]),
        ])
        out = run_agent("在不考虑库存的既定简化下，T1 H300最优组合？",
                        api_key="", base_url="", client=client)
        self.assertEqual(out.status, "verified_tool_output")

    def test_verified_t0_summary_distinguishes_total_and_unit_cost(self):
        answer = verified_tool_answer([{"name": "calculate_t0_abc", "status": "ok",
                                        "result": calculate_t0()}])
        self.assertIn("22,000.00元", answer)
        self.assertIn("220.00元", answer)
        self.assertIn("78,000.00元", answer)
        self.assertIn("780.00元", answer)

    def test_h350_requested_but_h300_executed_is_rejected(self):
        call = self.tool_call("optimize_t1", '{}')
        client = FakeClient([
            SimpleNamespace(content=None, tool_calls=[call]),
            SimpleNamespace(content="优化完成", tool_calls=[]),
        ])
        out = run_agent("T1 H350最优组合？", api_key="", base_url="", client=client)
        self.assertEqual(out.status, "intent_parameter_mismatch")
        self.assertIn("350", out.answer)

    def test_price_1700_requested_but_1800_executed_is_rejected(self):
        call = self.tool_call("optimize_t1", '{"overrides":{"price_per_unit":{"Y":1800}}}')
        client = FakeClient([
            SimpleNamespace(content=None, tool_calls=[call]),
            SimpleNamespace(content="优化完成", tool_calls=[]),
        ])
        out = run_agent("T1 Y降价到1700元的最优组合？",
                        api_key="", base_url="", client=client)
        self.assertEqual(out.status, "intent_parameter_mismatch")
        self.assertIn("Y", out.answer)

    def test_matching_explicit_h_and_y_price_are_accepted(self):
        record = {"name": "optimize_t1", "status": "ok", "result":
                  calculate_t1({"total_capacity_hours": 300,
                                "price_per_unit": {"Y": 1700}})}
        self.assertIsNone(verify_interpreted_parameters("H=300、Y售价为1700", [record]))

    def test_t1_comparison_request_cannot_be_answered_with_one_scenario_only(self):
        call = self.tool_call("optimize_t1", '{"overrides":{"price_per_unit":{"Y":1700}}}')
        client = FakeClient([
            SimpleNamespace(content=None, tool_calls=[call]),
            SimpleNamespace(content="已对比，下降很多。", tool_calls=[]),
        ])
        out = run_agent("T1 Y售价降到1700元，与H300原情景有何区别？",
                        api_key="", base_url="", client=client)
        self.assertEqual(out.status, "unverified_numeric_route")

    def test_company_name_in_simulated_t1_does_not_force_organic_fact_search(self):
        call = self.tool_call("optimize_t1", '{}')
        client = FakeClient([
            SimpleNamespace(content=None, tool_calls=[call]),
            SimpleNamespace(content="输出完成", tool_calls=[]),
        ])
        out = run_agent("中车四方案例的T1教学模拟在H300最优产量？",
                        api_key="", base_url="", client=client)
        self.assertEqual(out.status, "verified_tool_output")

    def test_real_enterprise_amount_cannot_be_satisfied_by_simulated_calculation(self):
        call = self.tool_call("optimize_t1", '{}')
        client = FakeClient([
            SimpleNamespace(content=None, tool_calls=[call]),
            SimpleNamespace(content="企业实际利润31,250元", tool_calls=[]),
        ])
        out = run_agent("中车四方真实利润是多少？", api_key="", base_url="", client=client)
        self.assertEqual(out.status, "invalid_fact_simulation_route")
        self.assertNotIn("31,250", out.answer)

    def test_real_enterprise_amount_fact_search_does_not_require_simulated_calculator(self):
        call = self.tool_call("lookup_case_evidence", '{"query":"真实产品Y亏损的订单具体编号"}')
        client = FakeClient([
            SimpleNamespace(content=None, tool_calls=[call]),
            SimpleNamespace(content="查不到资料", tool_calls=[]),
        ])
        out = run_agent("中车四方真实产品Y亏损了多少钱？",
                        api_key="", base_url="", client=client)
        self.assertEqual(out.status, "no_case_evidence")

    def test_2023_source_fact_query_not_misclassified_as_numeric(self):
        call = self.tool_call("lookup_case_evidence", '{"query":"ABC"}')
        client = FakeClient([
            SimpleNamespace(content=None, tool_calls=[call]),
            SimpleNamespace(content="据说公司利润千万。", tool_calls=[]),
        ])
        out = run_agent("中车四方2023年公开报道的ABC实践是什么？",
                        api_key="", base_url="", client=client)
        self.assertEqual(out.status, "verified_tool_output")
        self.assertIn("F-2023-ABC", out.answer)
        self.assertNotIn("利润千万", out.answer)
        self.assertEqual(out.tool_records[0]["arguments"]["query"],
                         "中车四方2023年公开报道的ABC实践是什么？")

    def test_broad_2023_case_practice_query_matches_all_existing_leads(self):
        records = lookup_handoff_facts("中车四方在2023年有哪些创新做法？")["matched"]
        self.assertEqual(len(records), 3)
        self.assertEqual(len({r["source_url"] for r in records}), 1)

    def test_real_contract_evidence_question_not_blocked_as_new_t1_rule(self):
        call = self.tool_call("lookup_case_evidence", '{"query":"ABC"}')
        client = FakeClient([
            SimpleNamespace(content=None, tool_calls=[call]),
            SimpleNamespace(content="没有资料。", tool_calls=[]),
        ])
        out = run_agent("中车四方实际合同订单的具体编号是什么？",
                        api_key="", base_url="", client=client)
        self.assertNotEqual(out.status, "unmodelled_business_rule")
        self.assertEqual(out.status, "no_case_evidence")

    def test_changed_demand_upper_limit_must_match_natural_language(self):
        call = self.tool_call("compare_t1_scenarios", '{}')
        client = FakeClient([
            SimpleNamespace(content=None, tool_calls=[call]),
            SimpleNamespace(content="完成", tool_calls=[]),
        ])
        out = run_agent("T1把Y需求上限改为80件后与基准对比？",
                        api_key="", base_url="", client=client)
        self.assertEqual(out.status, "intent_parameter_mismatch")
        self.assertIn("maximum_quantity.Y", out.answer)

    def test_changed_unit_outlay_must_match_natural_language(self):
        result = calculate_t1({"avoidable_variable_outlay_per_unit": {"Y": 1250}})
        mismatch = verify_interpreted_parameters(
            "T1把Y变动支出改为1200元，结果如何？",
            [{"name": "optimize_t1", "status": "ok", "result": result}])
        self.assertIn("avoidable_variable_outlay_per_unit.Y", mismatch)

    def test_from_old_to_new_unit_outlay_is_verified_against_new_value(self):
        correct = {"name": "optimize_t1", "status": "ok", "result":
                   calculate_t1({"avoidable_variable_outlay_per_unit": {"Y": 1250}})}
        question = "T1中Y变动支出从1150增加到1250元后，最优组合？"
        self.assertIsNone(verify_interpreted_parameters(question, [correct]))
        wrong = {"name": "optimize_t1", "status": "ok", "result": calculate_t1()}
        self.assertIn("1250", verify_interpreted_parameters(question, [wrong]))

    def test_unrequested_override_is_blocked(self):
        result = calculate_t1({"total_capacity_hours": 350, "price_per_unit": {"Y": 1700}})
        mismatch = verify_interpreted_parameters(
            "T1 H350最优组合是多少？",
            [{"name": "optimize_t1", "status": "ok", "result": result}])
        self.assertIn("price_per_unit.Y", mismatch)

    def test_multiple_conflicting_H_values_requires_clarification(self):
        result = calculate_t1({"total_capacity_hours": 350})
        mismatch = verify_interpreted_parameters(
            "T1 H350与H300对照解释一下",
            [{"name": "optimize_t1", "status": "ok", "result": result}])
        self.assertIn("多个不同数值", mismatch)

    def test_H_from_300_to_350_uses_new_target_not_old_baseline(self):
        record = {"name": "optimize_t1", "status": "ok",
                  "result": calculate_t1({"total_capacity_hours": 350})}
        self.assertIsNone(verify_interpreted_parameters(
            "把T1的H从300提高到350小时，重算最优组合。", [record]))
        self.assertIn("total_capacity_hours", verify_interpreted_parameters(
            "把T1的H从300提高到350小时，重算最优组合。",
            [{"name": "optimize_t1", "status": "ok", "result": calculate_t1()}]))

    def test_full_t0_summary_includes_two_stage_and_single_driver_contrast(self):
        answer = verified_tool_answer([{"name": "calculate_t0_abc", "status": "ok",
                                        "result": calculate_t0()}])
        self.assertIn("6,000.00元", answer)
        self.assertIn("1,000.00元", answer)
        self.assertIn("571.43元", answer)
        self.assertIn("428.57元", answer)

    def test_t1_comparison_summary_discloses_all_effective_fields(self):
        answer = verified_tool_answer([{"name": "compare_t1_scenarios", "status": "ok",
                                        "result": compare_t1({"price_per_unit": {"Y": 1700}})}])
        self.assertIn("单件加工工时=2.0/1.5", answer)
        self.assertIn("需求上限X/Y=100/100件", answer)
        self.assertIn("不能保证按每小时贡献率的简单贪心取整获得全局最优", answer)

    def test_infeasible_solution_has_no_fabricated_optimum(self):
        out = calculate_t1({"total_capacity_hours": 0,
                            "minimum_quantity": {"X": 1}})
        answer = verified_tool_answer([{"name": "optimize_t1", "status": "ok", "result": out}])
        self.assertIn("无可行整数", answer)
        self.assertNotIn("总边际贡献=", answer)

    def test_maximum_quantity_colloquial_phrase_cannot_be_ignored(self):
        record = {"name": "optimize_t1", "status": "ok", "result": calculate_t1()}
        mismatch = verify_interpreted_parameters(
            "T1把Y最多生产80件，最优组合是什么？", [record])
        self.assertIn("maximum_quantity.Y", mismatch)
        mismatch2 = verify_interpreted_parameters(
            "T1 X最多80件、Y最多70件，重新优化", [record])
        self.assertIn("maximum_quantity.X", mismatch2)

    def test_minimum_quantity_colloquial_phrase_cannot_be_ignored(self):
        record = {"name": "optimize_t1", "status": "ok", "result": calculate_t1()}
        self.assertIn("minimum_quantity.X", verify_interpreted_parameters(
            "T1要求X至少生产20件，重新优化", [record]))

    def test_general_capacity_label_cannot_be_ignored(self):
        record = {"name": "optimize_t1", "status": "ok", "result": calculate_t1()}
        self.assertIn("total_capacity_hours", verify_interpreted_parameters(
            "T1把总工时改为350小时后最优组合？", [record]))

    def test_price_set_to_synonym_cannot_be_ignored(self):
        record = {"name": "optimize_t1", "status": "ok", "result": calculate_t1()}
        self.assertIn("price_per_unit.Y", verify_interpreted_parameters(
            "T1将Y售价改成1700元，重新求最优", [record]))

    def test_wrong_same_field_other_product_override_is_blocked(self):
        # 提问只授权Y改价，X价格被一起更改是答非所问，不能因出现“售价”就放行。
        forged = calculate_t1({"price_per_unit": {"X": 1600, "Y": 1700}})
        mismatch = verify_interpreted_parameters(
            "T1把Y售价改为1700元，求最优", [{"name": "optimize_t1", "status": "ok", "result": forged}])
        self.assertIn("price_per_unit.X", mismatch)

    def test_only_requested_product_price_change_is_accepted(self):
        correct = calculate_t1({"price_per_unit": {"Y": 1700}})
        self.assertIsNone(verify_interpreted_parameters(
            "T1把Y售价改为1700元，求最优", [{"name": "optimize_t1", "status": "ok", "result": correct}]))

    def test_relative_capacity_amount_asks_for_absolute_target_before_api(self):
        client = FakeClient([])
        out = run_agent("T1把H增加50小时后重新求最优", api_key="", base_url="", client=client)
        self.assertEqual(out.status, "relative_adjustment_needs_target")
        self.assertFalse(client.completions.received)

    def test_relative_price_amount_asks_for_absolute_target_before_api(self):
        client = FakeClient([])
        out = run_agent("T1将Y售价下降200元后求最优", api_key="", base_url="", client=client)
        self.assertEqual(out.status, "relative_adjustment_needs_target")
        self.assertFalse(client.completions.received)

    def test_explicit_from_to_target_does_not_trigger_relative_refusal(self):
        call = self.tool_call("optimize_t1", '{"overrides":{"total_capacity_hours":350}}')
        client = FakeClient([
            SimpleNamespace(content=None, tool_calls=[call]),
            SimpleNamespace(content="完成", tool_calls=[]),
        ])
        out = run_agent("T1把H从300提高到350，重新求最优", api_key="", base_url="", client=client)
        self.assertEqual(out.status, "verified_tool_output")
        self.assertIn("X=100件，Y=100件", out.answer)

    def test_contextual_followup_cannot_silently_reset_history(self):
        client = FakeClient([])
        history = [{"role": "user", "content": "T1 H350最优是什么？"},
                   {"role": "assistant", "content": "X100 Y100"}]
        out = run_agent("那再把Y售价改为1700，最优组合呢？",
                        api_key="", base_url="", history=history, client=client)
        self.assertEqual(out.status, "context_requires_explicit_parameters")
        self.assertFalse(client.completions.received)

    def test_explicit_new_baseline_is_allowed_in_multi_turn(self):
        call = self.tool_call("optimize_t1", '{}')
        client = FakeClient([
            SimpleNamespace(content=None, tool_calls=[call]),
            SimpleNamespace(content="完成", tool_calls=[]),
        ])
        out = run_agent("重新按基准：T1 H300最优是什么？",
                        api_key="", base_url="", history=[{"role": "user", "content": "H350"}],
                        client=client)
        self.assertEqual(out.status, "verified_tool_output")

    def test_matching_abc_article_does_not_claim_true_profit_amount(self):
        call = self.tool_call("lookup_case_evidence", '{"query":"中车四方实际利润是多少"}')
        client = FakeClient([
            SimpleNamespace(content=None, tool_calls=[call]),
            SimpleNamespace(content="公司利润999亿元", tool_calls=[]),
        ])
        out = run_agent("中车四方ABC报道是否披露真实利润金额是多少？",
                        api_key="", base_url="", client=client)
        self.assertEqual(out.status, "real_enterprise_amount_not_available")
        self.assertNotIn("999", out.answer)

    def test_actual_abc_management_practice_is_not_rejected_as_profit_request(self):
        call = self.tool_call("lookup_case_evidence", '{"query":"作业成本"}')
        client = FakeClient([
            SimpleNamespace(content=None, tool_calls=[call]),
            SimpleNamespace(content="公司作业成本管理实践", tool_calls=[]),
        ])
        out = run_agent("中车四方实际ABC成本管理实践有哪些？",
                        api_key="", base_url="", client=client)
        self.assertEqual(out.status, "verified_tool_output")
        self.assertIn("F-2023-ABC", out.answer)


if __name__ == "__main__":
    unittest.main()
