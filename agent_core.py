"""中车四方案例教学智能体的受控工具层与模型协调层（开发版）。

本文件不修改 v2.2 冻结数据；所有实际计算交由随附的 v2.2 原始内核执行。
目前只为已有交接资料建立本地检索入口，不会联网自动补充企业事实。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
BASELINE_DIR = ROOT / "baseline_v2_2"
DATA_FILE = BASELINE_DIR / "中车四方_教学参数_v2_2.json"
ENGINE_FILE = BASELINE_DIR / "中车四方_确定性核算内核_v2_2.py"
FACTS_FILE = ROOT / "knowledge" / "handoff_claims.json"
VERSION = "agent-v0.1.4-interface-only"
MAX_TOOL_CALLS = 4
MAX_INPUT_CHARS = 2500


class AgentInputError(ValueError):
    """工具输入超出冻结教学模型合同。"""


class AgentRuntimeError(RuntimeError):
    """大模型或其工具调用发生可报告的技术故障。"""


def _load_baseline_engine():
    spec = importlib.util.spec_from_file_location("frozen_v22_engine", ENGINE_FILE)
    if spec is None or spec.loader is None:
        raise AgentRuntimeError("无法加载已冻结的v2.2核算内核")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ENGINE = _load_baseline_engine()


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise AgentInputError(f"标准JSON包含重复字段：{key}")
        out[key] = value
    return out


def load_frozen_data() -> dict[str, Any]:
    with DATA_FILE.open(encoding="utf-8") as handle:
        data = json.load(
            handle,
            object_pairs_hook=_no_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                AgentInputError(f"标准JSON含非法常量：{value}")
            ),
        )
    ENGINE.validate_baseline_expectations(data)
    return data


def get_identity() -> dict[str, str]:
    data = load_frozen_data()
    return {
        "agent_version": VERSION,
        "baseline_version": data["meta"]["version"],
        "entity": data["entity"]["name"],
        "nature": "作者教学假设；并非企业账目或真实经营决策系统",
        "data_sha256": hashlib.sha256(DATA_FILE.read_bytes()).hexdigest(),
        "engine_sha256": hashlib.sha256(ENGINE_FILE.read_bytes()).hexdigest(),
    }


def baseline_scenario() -> dict[str, Any]:
    data = load_frozen_data()
    return {
        "total_capacity_hours": data["T1"]["base_total_bottleneck_hours"],
        "price_per_unit": dict(data["T1"]["price_per_unit_base"]),
    }


SUPPORTED_DYNAMIC_FIELDS = frozenset({
    "total_capacity_hours", "price_per_unit",
    "avoidable_variable_outlay_per_unit", "minimum_quantity",
    "maximum_quantity", "bottleneck_processing_hours_per_unit",
    "additional_fixed_outlay_for_scenario",
})


def build_scenario(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """构建独立、受控情景，不修改v2.2 JSON，不添加新业务条件。"""
    overrides = {} if overrides is None else overrides
    if not isinstance(overrides, dict):
        raise AgentInputError("情景参数必须是对象")
    unknown = set(overrides) - SUPPORTED_DYNAMIC_FIELDS
    if unknown:
        raise AgentInputError(f"未建模的情景字段：{sorted(unknown)}；拒绝静默忽略")
    scenario = baseline_scenario()
    defaults = load_frozen_data()["T1"]
    for key, value in overrides.items():
        if key in {
            "price_per_unit", "avoidable_variable_outlay_per_unit",
            "minimum_quantity", "maximum_quantity", "bottleneck_processing_hours_per_unit",
        }:
            if not isinstance(value, dict) or not value or set(value) - {"X", "Y"}:
                raise AgentInputError(f"{key}必须指定至少一个有效产品X或Y")
            reference = (scenario["price_per_unit"] if key == "price_per_unit"
                         else defaults[key])
            scenario[key] = {**reference, **value}
        else:
            scenario[key] = value
    # 由已审计内核负责最终的金额、件数、工时、可行性校验。
    return scenario


def calculate_t0() -> dict[str, Any]:
    data = load_frozen_data()
    result = ENGINE.verify_t0(data)
    return {
        "model": "T0-ABC-two-stage",
        "nature": "假设性教学情景，并非中车四方真实财务数据",
        "result": result,
        "data_version": data["meta"]["version"],
    }


def calculate_t1(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    data = load_frozen_data()
    scenario = build_scenario(overrides)
    result = ENGINE.optimize_t1(data, scenario)
    return {
        "model": "T1-integer-relevant-cost-optimization",
        "nature": "独立T1期间的作者教学假设；不自动重算T0 ABC",
        "scenario": scenario,
        "effective_inputs": resolved_t1_inputs(scenario, data),
        "result": result,
        "data_version": data["meta"]["version"],
    }


def resolved_t1_inputs(scenario: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    """列出实际入模的七类字段，防止未在scenario里重复的默认值被误读为缺失。"""
    source = data["T1"]
    return {
        "total_capacity_hours": scenario["total_capacity_hours"],
        "price_per_unit": dict(scenario["price_per_unit"]),
        "avoidable_variable_outlay_per_unit": dict(scenario.get(
            "avoidable_variable_outlay_per_unit", source["avoidable_variable_outlay_per_unit"])),
        "bottleneck_processing_hours_per_unit": dict(scenario.get(
            "bottleneck_processing_hours_per_unit", source["bottleneck_processing_hours_per_unit"])),
        "minimum_quantity": dict(scenario.get("minimum_quantity", source["minimum_quantity"])),
        "maximum_quantity": dict(scenario.get("maximum_quantity", source["maximum_quantity"])),
        "additional_fixed_outlay_for_scenario": scenario.get("additional_fixed_outlay_for_scenario", 0),
    }


def compare_t1(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    current = calculate_t1(overrides)
    initial = calculate_t1()
    a, b = initial["result"], current["result"]
    if a["status"] != "optimal" or b["status"] != "optimal":
        delta: dict[str, Any] | None = None
    else:
        delta = {
            "x_change": b["x"] - a["x"],
            "y_change": b["y"] - a["y"],
            "contribution_change": b["total_contribution"] - a["total_contribution"],
            "period_residual_change": b["simplified_period_residual"] - a["simplified_period_residual"],
        }
    return {"baseline": initial, "modified": current, "difference": delta}


def lookup_handoff_facts(query: str) -> dict[str, Any]:
    """仅返回交接包中已有的企业资料条目；不进行开放网络搜索。"""
    if not isinstance(query, str) or not query.strip():
        raise AgentInputError("事实检索查询不能为空")
    with FACTS_FILE.open(encoding="utf-8") as handle:
        records = json.load(handle)["records"]
    terms = [t for t in re.split(r"[\W_]+", query.lower()) if len(t) > 1]
    scored = []
    for record in records:
        score = sum(1 for word in record["keywords"] if word.lower() in query.lower())
        score += sum(1 for term in terms if term in record["summary"].lower())
        if score:
            scored.append((score, record))
    scored.sort(key=lambda item: (-item[0], item[1]["id"]))
    if not scored and re.search(r"中车|四方", query) and re.search(r"有哪些|做法|创新|概况|介绍|2023年|主要实践", query):
        # 泛问既有公开实践，可返回全部已批准摘要；不代表穷尽企业创新。
        if not re.search(r"订单|利润|具体金额|具体数据|财务数据|数值|收入|亏损|营收|财报|件数|资产负债|202[4-9]", query):
            scored = [(1, record) for record in records]
    return {
        "source_policy": "只读取原v2.2交接手册已列示资料；未自主新增企业来源",
        "matched": [r for _, r in scored[:4]],
        "status": "matched" if scored else "no_matching_approved_handoff_excerpt",
        "note": "交接记录属于既有证据入口；正式企业事实陈述仍需按原文核对。",
    }


TOOL_DEFINITIONS = [
    {"type": "function", "function": {
        "name": "calculate_t0_abc",
        "description": "计算T0两阶段作业成本归集、费用分摊与内部分析余额。只针对作者教学假设，不代表企业实账。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "optimize_t1",
        "description": "严格校验输入后，按已审计的有界整数算法重新计算T1情景。允许只修改X或Y某一个价格，另一产品保留标准基线；无参数表示标准基准。",
        "parameters": {"type": "object", "properties": {
            "overrides": {"type": "object", "description": "仅支持既定模型的动态字段；只改Y价格时写price_per_unit:{Y:1700}，其他值沿用原基线。",
                          "properties": {
                              "total_capacity_hours": {"type": "number"},
                              "price_per_unit": {"type": "object", "properties": {
                                  "X": {"type": "number"}, "Y": {"type": "number"}}},
                              "avoidable_variable_outlay_per_unit": {"type": "object", "properties": {
                                  "X": {"type": "number"}, "Y": {"type": "number"}}},
                              "minimum_quantity": {"type": "object", "properties": {
                                  "X": {"type": "integer"}, "Y": {"type": "integer"}}},
                              "maximum_quantity": {"type": "object", "properties": {
                                  "X": {"type": "integer"}, "Y": {"type": "integer"}}},
                              "bottleneck_processing_hours_per_unit": {"type": "object", "properties": {
                                  "X": {"type": "number"}, "Y": {"type": "number"}}},
                              "additional_fixed_outlay_for_scenario": {"type": "number"},
                          }, "additionalProperties": False},
        }, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "compare_t1_scenarios",
        "description": "对比T1标准基准与用户有效调整后的最优组合、边际贡献、简化期间余额及差额。",
        "parameters": {"type": "object", "properties": {
            "overrides": {"type": "object", "description": "只接收原有T1动态字段；如只修改Y售价，可传{price_per_unit:{Y:1700}}。",
                          "properties": {
                              "total_capacity_hours": {"type": "number"},
                              "price_per_unit": {"type": "object", "properties": {
                                  "X": {"type": "number"}, "Y": {"type": "number"}}},
                              "avoidable_variable_outlay_per_unit": {"type": "object", "properties": {
                                  "X": {"type": "number"}, "Y": {"type": "number"}}},
                              "minimum_quantity": {"type": "object", "properties": {
                                  "X": {"type": "integer"}, "Y": {"type": "integer"}}},
                              "maximum_quantity": {"type": "object", "properties": {
                                  "X": {"type": "integer"}, "Y": {"type": "integer"}}},
                              "bottleneck_processing_hours_per_unit": {"type": "object", "properties": {
                                  "X": {"type": "number"}, "Y": {"type": "number"}}},
                              "additional_fixed_outlay_for_scenario": {"type": "number"},
                          }, "additionalProperties": False},
        }, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "lookup_case_evidence",
        "description": "检索原交接包已列示的中车四方公开资料摘要及来源编号，禁止凭空发明真实成本。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
        }, "required": ["query"], "additionalProperties": False},
    }},
]


def execute_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """白名单分派：语言模型不能直接执行任意Python代码。"""
    if not isinstance(arguments, dict):
        raise AgentInputError("工具参数必须是JSON对象")
    if name == "calculate_t0_abc":
        if arguments:
            raise AgentInputError("T0基准工具不接受动态参数；历史教学假设冻结")
        return calculate_t0()
    if name in {"optimize_t1", "compare_t1_scenarios"}:
        if set(arguments) - {"overrides"}:
            raise AgentInputError("T1工具只接受overrides参数")
        overrides = arguments.get("overrides", {})
        return calculate_t1(overrides) if name == "optimize_t1" else compare_t1(overrides)
    if name == "lookup_case_evidence":
        if set(arguments) != {"query"}:
            raise AgentInputError("资料检索工具必须且只能提供query")
        return lookup_handoff_facts(arguments["query"])
    raise AgentInputError(f"未知工具{name}；禁止执行")


@dataclass
class AgentTurn:
    answer: str
    tool_records: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""
    status: str = "ok"


SYSTEM_INSTRUCTION = """你是大学管理会计案例项目的工具型决策辅助智能体，不是企业实际生产指挥系统。
真实研究主体：中车青岛四方机车车辆股份有限公司；教学产品X/Y、T0/T1全部金额和参数是作者假设。
企业事实只能依据lookup_case_evidence检索到的交接资料说明，不得将其说成企业真实财务数据，也不得虚构企业实施了本组算法。
凡涉及T0 ABC、T1最优组合、价格、需求、工时、边际贡献、金额、比较，必须调用相应Python工具。不得心算、估算或直接编造结果。
T0为历史教学归集域，T1为独立短期决策域；T0的X/Y支持费用总分摊额分别为22000/78000元，折合单件220/780元；不得把任一T0分摊额加进T1相关成本，不得跨期重复扣10万元固定费用。
不支持合同约束、库存、加班分段成本、额外质检或真实企业订单指令；遇到新增业务条件，必须指出模型外边界，不可假装已纳入计算。
工具返回错误时应解释输入问题并请求更正，不可忽略错误直接报结果。
计算与企业事实的正式输出由程序根据工具记录独立生成；你的自然语言回复仅用于一般性解释，不得绕过程序的核验。
只展示工具实际返回的数值；若没有足够企业来源或工具输出，请明确不足。回答简洁清楚，附明教学假设及证据边界。
"""


def _requires_numeric_tool(question: str) -> bool:
    # 年份（如2023）本身不是数值计算意图；明确的企业事实询问不应
    # 被T0/T1教学模型强制接管。
    if _is_fact_only_question(question):
        return False
    return bool(re.search(
        r"T0|T1|最优|优化|计算|核算|分摊|工时|产能|调价|降价|售价|"
        r"边际贡献|余额|X/Y|X与Y|产量|需求|接单|停产|单位成本|变动支出|决策|利润|价格|生产组合",
        question, re.I,
    ))


def _is_fact_only_question(question: str) -> bool:
    if not _requires_evidence_tool(question):
        return False
    return not bool(re.search(
        r"T0|T1|教学|模拟|假设|情景|最优|求解|算一下|重新计算|"
        r"[XY]产品|[XY]售价|[XY]需求|[XY]变动|X/Y|[XY]单件|"
        r"(?<![A-Za-z0-9_])H\s*[=＝:：]?\s*\d+",
        question, re.I,
    ))


def _requires_evidence_tool(question: str) -> bool:
    explicit_facts = bool(re.search(
        r"真实|实际|公开|报道|披露|企业创新|公司做法|经营实践|案例事实|文献出处|真实业务|真实数据",
        question,
    ))
    if explicit_facts:
        return True
    # 明确在讨论T0/T1作者模拟模型时，提及研究对象的名称并不自动
    # 等于要查询企业真实账目；否则会错误地强制要求执行无关的事实检索。
    if re.search(r"T0|T1|教学|模拟|假设|情景|X/Y", question, re.I):
        return False
    return bool(re.search(r"中车|四方|企业|公司", question))


def _real_enterprise_amount_request(question: str) -> bool:
    """真实公司财务数据的求证不能被重定向到假设性X/Y计算器。"""
    return bool(
        re.search(r"真实|实际|财报|披露|公司账目|经营业绩", question)
        and re.search(r"利润|亏损|营收|收入|费用|成本|订单|净利|毛利", question)
        and re.search(r"多少|金额|数额|具体数字|具体金额|真实利润|实际利润|"
                      r"净利润|利润率|收入额|营收额|单位成本|财报数字|经营业绩", question)
        and not re.search(r"T0|T1|教学|模拟|假设|情景", question, re.I)
    )


def _unmodelled_business_rule(question: str) -> str | None:
    """遇到明示的模型外业务条件时，不允许以旧方程假装已经考虑。"""
    if _is_fact_only_question(question):
        return None
    # "不考虑库存/无加班"只是确认既有简化假设，不应误判成新增业务约束。
    scoped_question = question
    for explicitly_excluded in (
        "不考虑库存", "没有库存", "无库存", "不涉及库存", "库存为零",
        "没有加班", "无加班", "不加班", "不增加质检",
        "没有合同", "无合同", "不考虑既签订单", "无已签订单",
    ):
        scoped_question = scoped_question.replace(explicitly_excluded, "")
    rules = {
        "合同或既签强制交付": r"合同|签约|已签|强制订单|保底订单",
        "加班或分段扩产费用": r"加班|阶梯产能|分段扩产|分段费用",
        "库存与跨期产销": r"库存|存货|跨期结转|产销不一致",
        "新增质检约束": r"质检变化|额外质检|增加质检|检验要求变化",
        "真实企业订单指令": r"真实订单|实际下单|真实接单|生产指令",
    }
    for label, pattern in rules.items():
        if re.search(pattern, scoped_question):
            return label
    return None


def _amount(value: int | float) -> str:
    return f"{value:,.2f}元"


def _recognizable_requested_parameters(question: str) -> tuple[dict[str, Decimal], set[str]]:
    """只核对语法可准确定位的数值。重复而冲突的数值标记为歧义，不猜先后。"""
    num = r"(\d+(?:\.\d+)?)"
    # “H从300提高到350”中300明确是旧值；先转换为目标H350以防错抓旧值。
    normalized = re.sub(
        r"(?<![A-Za-z0-9_])H\s*从\s*\d+(?:\.\d+)?\s*"
        r"(?:提高|增加|改|调整|恢复|降低|缩减)\s*(?:到|至|为)\s*(\d+(?:\.\d+)?)",
        lambda match: "H" + match.group(1), question, flags=re.I,
    )
    normalized = re.sub(
        r"(售价|价格|销售单价|变动支出|变动成本|变动费用|需求上限|最大需求|"
        r"最低产量|最低数量|单件工时|每件工时)\s*"
        r"从\s*\d+(?:\.\d+)?\s*(?:提高|增加|改|调整|恢复|降低|降|升|缩减)"
        r"\s*(?:到|至|为)\s*(\d+(?:\.\d+)?)",
        lambda match: match.group(1) + "改为" + match.group(2), normalized,
    )
    action = r"(?:为|是|=|＝|：|:|改为|改成|设置为|设为|调整为|调为|降到|降至|增加到|增加至|提高到|提高至|变为|变成)\s*"
    patterns: dict[str, list[str]] = {
        "total_capacity_hours": [
            rf"(?<![A-Za-z0-9_])H\s*(?:[=＝:：]\s*)?{num}",
            rf"(?:总可用|总瓶颈|瓶颈|可用|总加工)工时\s*{action}{num}",
            rf"(?:总工时|总产能|瓶颈产能|总加工能力|可用工时|瓶颈工时|H)\s*{action}{num}",
        ],
    }
    for product in ("X", "Y"):
        prefix = rf"(?:产品)?{product}(?:的)?"
        patterns[f"price_per_unit.{product}"] = [
            rf"{prefix}.{{0,8}}?(?:售价|销售单价|价格)\s*{action}{num}",
            rf"{prefix}.{{0,8}}?(?:降价到|降价至|降到|降至|调价到|调价至){num}",
        ]
        patterns[f"maximum_quantity.{product}"] = [
            rf"{prefix}.{{0,4}}?(?:需求上限|最大需求|最高需求|最大产量)\s*{action}{num}",
            rf"{prefix}.{{0,5}}?(?:最多|至多|不超过)\s*(?:生产|销售|卖|做)?\s*{num}\s*件?",
        ]
        patterns[f"minimum_quantity.{product}"] = [
            rf"{prefix}.{{0,4}}?(?:最低产量|产量下限|最低数量)\s*{action}{num}",
            rf"{prefix}.{{0,5}}?(?:至少|最少|不得少于)\s*(?:生产|销售|卖|做)?\s*{num}\s*件?",
        ]
        patterns[f"avoidable_variable_outlay_per_unit.{product}"] = [
            rf"{prefix}.{{0,4}}?(?:单位)?(?:相关)?变动(?:成本|支出|费用)\s*{action}{num}",
        ]
        patterns[f"bottleneck_processing_hours_per_unit.{product}"] = [
            rf"{prefix}.{{0,4}}?(?:单件|每件|单位)(?:瓶颈)?(?:加工)?(?:工时|耗时|小时)\s*{action}{num}",
        ]
    patterns["additional_fixed_outlay_for_scenario"] = [
        rf"(?:新增|额外)(?:相关)?(?:固定)?(?:支出|费用|成本)\s*{action}{num}",
    ]
    requested: dict[str, Decimal] = {}
    ambiguous: set[str] = set()
    for field_name, forms in patterns.items():
        values: set[Decimal] = set()
        for form in forms:
            values.update(Decimal(value) for value in re.findall(form, normalized, re.I))
        if len(values) == 1:
            requested[field_name] = next(iter(values))
        elif len(values) > 1:
            ambiguous.add(field_name)
    return requested, ambiguous


def _requires_explicit_relative_target(question: str) -> bool:
    """增减幅度不是最终绝对值；不能让模型偷偷当成已知H/售价等目标值。"""
    return bool(re.search(
        r"(?:H|总工时|瓶颈工时|产能|售价|价格|需求|上限|最低产量|"
        r"变动支出|变动成本|单件工时|固定支出|固定成本)"
        r".{0,12}?(?:增加|减少|提高|降低|上涨|下降|涨|降|增|减)"
        r"\s*(?:了)?\s*\d+(?:\.\d+)?\s*(?:元|件|小时|%|％)?",
        question, re.I,
    ))


def _is_implicit_followup(question: str, history: list[dict[str, str]] | None) -> bool:
    """未设计跨轮情景状态：遇到明显承接语句先澄清，不能默认继承或重置。"""
    if not history or not _requires_numeric_tool(question):
        return False
    if re.search(r"独立新情景|重新从基准|重新按基准|重置基准|标准基准", question):
        return False
    return bool(re.search(
        r"^(?:那|那么|然后|接着|继续|再按|如果这样)|"
        r"刚才|上次|上一轮|在此基础上|沿用上轮|沿用之前|保持其他不变|其他条件同上|"
        r"按刚才|以上一轮", question,
    ))


def verify_interpreted_parameters(question: str, records: list[dict[str, Any]]) -> str | None:
    """拦截自然语言意图与实际提交给优化器的关键参数明显不一致的情况。"""
    requested, ambiguous = _recognizable_requested_parameters(question)
    if ambiguous:
        return (f"同一参数在提问中出现多个不同数值：{', '.join(sorted(ambiguous))}。"
                "请明确调整后的目标值及原基准值，避免误把对照数值当成新输入。")
    for record in records:
        if record["status"] != "ok" or record["name"] not in {
            "optimize_t1", "compare_t1_scenarios",
        }:
            continue
        payload = record["result"]
        inputs = (payload["effective_inputs"] if record["name"] == "optimize_t1"
                  else payload["modified"]["effective_inputs"])
        for key, expected in requested.items():
            if "." in key:
                field_name, product = key.split(".", 1)
                actual = inputs[field_name][product]
            else:
                actual = inputs[key]
            if Decimal(str(actual)) != expected:
                return f"用户明确指定{key}={expected}，但模型传入计算器的是{actual}；已阻止输出可能答非所问的结论。"
        # 即使明示参数核对通过，模型仍可能同时修改“同一字段的另一产品”。
        # 因此逐产品比对每个实际变更是否有可识别且已验证的明确目标值。
        # 未识别的复杂表达宁可请求用户重述，也不默许擅改。
        baseline = resolved_t1_inputs(baseline_scenario(), load_frozen_data())
        changes = []
        for field_name, val in inputs.items():
            if isinstance(val, dict):
                for product in ("X", "Y"):
                    if (Decimal(str(val[product])) != Decimal(str(baseline[field_name][product]))
                            and f"{field_name}.{product}" not in requested):
                        changes.append(f"{field_name}.{product}")
            else:
                if (Decimal(str(val)) != Decimal(str(baseline[field_name]))
                        and field_name not in requested):
                    changes.append(field_name)
        if changes:
            return ("模型还修改了提问中没有明确、可核验目标值的情景字段："
                    + "、".join(changes) + "；已阻止以错误情景输出正式结论。")
    return None


def _format_optimum(result: dict[str, Any]) -> str:
    if result["status"] != "optimal":
        return f"无可行整数生产组合。原因：{result.get('reason', '模型约束不相容')}。"
    tie = (f"并列最优解共有{result['optimal_solution_count']}组，以下仅展示其中一组。"
           if result["optimal_solution_count"] > 1 else "")
    return (
        f"X={result['x']}件，Y={result['y']}件；"
        f"总边际贡献={_amount(result['total_contribution'])}；"
        f"简化期间余额={_amount(result['simplified_period_residual'])}；"
        f"已使用瓶颈工时={result['capacity_used_hours']}小时，"
        f"剩余={result['capacity_slack_hours']}小时。{tie}"
    )


def _format_t1_inputs(payload: dict[str, Any]) -> str:
    """把求解实际使用的默认+调整参数全部展示，避免只展示价格导致的错觉。"""
    p = payload["effective_inputs"]
    return (
        f"H={p['total_capacity_hours']}小时；X/Y售价="
        f"{_amount(p['price_per_unit']['X'])}/{_amount(p['price_per_unit']['Y'])}；"
        f"X/Y单位相关变动支出="
        f"{_amount(p['avoidable_variable_outlay_per_unit']['X'])}/"
        f"{_amount(p['avoidable_variable_outlay_per_unit']['Y'])}；"
        f"X/Y单件加工工时={p['bottleneck_processing_hours_per_unit']['X']}/"
        f"{p['bottleneck_processing_hours_per_unit']['Y']}小时；"
        f"最低数量X/Y={p['minimum_quantity']['X']}/{p['minimum_quantity']['Y']}件；"
        f"需求上限X/Y={p['maximum_quantity']['X']}/{p['maximum_quantity']['Y']}件；"
        f"整项情景新增固定支出={_amount(p['additional_fixed_outlay_for_scenario'])}。"
    )


def _format_t1_reasoning(payload: dict[str, Any]) -> str:
    r = payload["result"]
    if r["status"] != "optimal":
        return "本情景约束无可行整数解，不能继续把某一件数组合称为最优。"
    c = r["unit_contribution"]
    return (
        "目标函数为 max[(X售价−X单位相关变动支出)×x"
        "+(Y售价−Y单位相关变动支出)×y]；"
        f"X/Y单位相关边际贡献={_amount(c['X'])}/{_amount(c['Y'])}。"
        "在工时、需求与整数件数约束下比较所有允许的组合；"
        "不能用历史ABC单位分摊额代替相关变动支出，"
        "也不能保证按每小时贡献率的简单贪心取整获得全局最优。"
    )


def verified_tool_answer(records: list[dict[str, Any]]) -> str:
    """只从确定性工具返回值构造正式数值/案例事实，不采用模型自述数字。"""
    lines: list[str] = []
    for record in records:
        if record["status"] != "ok":
            continue
        name, payload = record["name"], record["result"]
        if name == "calculate_t0_abc":
            r = payload["result"]
            lines.append(
                "**T0 两阶段 ABC 已核算**："
                f"支持人员归集率{_amount(r['resource_rates']['staff_hour'])}/人员小时，"
                f"支持设备归集率{_amount(r['resource_rates']['support_equipment_hour'])}/设备小时；"
                f"准备作业成本池{_amount(r['pools']['preparation'])}、"
                f"检验支持作业成本池{_amount(r['pools']['inspection_support'])}；"
                f"准备/检验动因费率为{_amount(r['activity_rates']['preparation'])}/次、"
                f"{_amount(r['activity_rates']['inspection_support'])}/次；"
                f"X总分摊{_amount(r['allocated_support_cost']['X'])}"
                f"（单件{_amount(r['allocated_unit_support_cost']['X'])}）、"
                f"Y总分摊{_amount(r['allocated_support_cost']['Y'])}"
                f"（单件{_amount(r['allocated_unit_support_cost']['Y'])}）；"
                f"单一按件均摊X/Y各{_amount(r['unit_allocation_by_quantity']['X'])}/件，"
                f"按总瓶颈加工工时对照X/Y各"
                f"{_amount(r['unit_allocation_by_processing_hours']['X'])}/"
                f"{_amount(r['unit_allocation_by_processing_hours']['Y'])}/件；"
                f"单位内部分析余额X为{_amount(r['unit_internal_residual']['X'])}、"
                f"Y为{_amount(r['unit_internal_residual']['Y'])}；"
                f"简化期间余额{_amount(r['simplified_period_residual'])}。"
                "成本流向为资源费用→准备/检验支持作业池→按两类不同动因分配至X/Y，"
                "总费用100,000元前后守恒。后一个工时单一动因只是人为对照，非支持设备耗时。"
                "上述分摊不代表可避免现金支出，Y的内部分析余额为负不代表应直接停产。"
            )
        elif name == "optimize_t1":
            lines.append(
                "**T1 整数优化已核算**：" + _format_t1_inputs(payload)
                + _format_optimum(payload["result"])
                + _format_t1_reasoning(payload)
                + "这仅是作者情景内的最优，非企业实际生产指令。"
            )
        elif name == "compare_t1_scenarios":
            lines.append(
                "**本轮调整的T1全部有效输入**："
                + _format_t1_inputs(payload["modified"])
            )
            lines.append(
                "**T1 标准情景**：" + _format_optimum(payload["baseline"]["result"])
                + "\n\n**T1 调整后情景**：" + _format_optimum(payload["modified"]["result"])
            )
            difference = payload["difference"]
            if difference is not None:
                lines.append(
                    f"**与基准相比**：X变化{difference['x_change']:+d}件、"
                    f"Y变化{difference['y_change']:+d}件，"
                    f"总边际贡献变化{_amount(difference['contribution_change'])}，"
                    f"简化期间余额变化{_amount(difference['period_residual_change'])}。"
                )
            lines.append(
                "**比较的会计解释**：" + _format_t1_reasoning(payload["modified"])
                + "最优组合的变化由改变后的贡献与工时、需求及整数约束共同决定，"
                "差额只是模型内情景差异，不等于现实中已实现节约或利润。"
                "不自动重算T0历史ABC。"
            )
        elif name == "lookup_case_evidence":
            matches = payload["matched"]
            if not matches:
                lines.append("既有交接资料未检索到相关企业事实证据；不能据此编造企业实践。")
            else:
                lines.append("**既有交接材料中的企业公开报道线索（原文仍须复核）**：")
                for fact in matches:
                    lines.append(
                        f"- [{fact['id']}] {fact['summary']} "
                        f"来源：{fact['source_title']}（{fact['source_date']}），"
                        f"[原有来源入口]({fact['source_url']})。"
                    )
    if not lines:
        return "本轮没有获得可以核验的工具结果，未生成正式计算或事实结论。"
    return "\n\n".join(lines) + "\n\n*以上X/Y金额为作者教学假设，所有数值由Python工具生成。*"


def _get_message_field(message: Any, name: str, default: Any = None) -> Any:
    return message.get(name, default) if isinstance(message, dict) else getattr(message, name, default)


def run_agent(
    question: str,
    *,
    api_key: str,
    base_url: str,
    model: str = "qwen3.7-flash",
    history: list[dict[str, str]] | None = None,
    client: Any = None,
) -> AgentTurn:
    """真正调用兼容OpenAI的模型API和白名单工具；允许注入假客户端做离线测试。"""
    if not isinstance(question, str) or not question.strip():
        raise AgentInputError("请先输入问题")
    if len(question) > MAX_INPUT_CHARS:
        raise AgentInputError(f"单次提问超过{MAX_INPUT_CHARS}字的安全限制")
    if _is_implicit_followup(question, history):
        return AgentTurn(
            "本工具暂未实现跨轮情景状态继承；您这句提问引用了上轮条件。"
            "为防止把上轮H、售价或需求错误恢复为默认值，请在本轮明确写出所有要保留的参数，"
            "或注明‘重新按基准’另起独立情景后再问。",
            status="context_requires_explicit_parameters",
        )
    if _requires_numeric_tool(question) and _requires_explicit_relative_target(question):
        return AgentTurn(
            "本轮提问使用了‘增加/降低若干元、件或小时’等相对变动幅度。"
            "为保证无上下文歧义，请改写为调整后的绝对值，例如‘T1 H=350小时’"
            "或‘T1 Y售价=1700元’，当前不按猜测的目标值计算。",
            status="relative_adjustment_needs_target",
        )
    out_of_scope = _unmodelled_business_rule(question)
    if out_of_scope:
        return AgentTurn(
            f"本问题涉及【{out_of_scope}】，目前冻结的T0/T1教学模型没有对应的输入字段或约束，"
            "不能把已有最优解当作考虑该条件后的结论。如需研究，请先明确新增假设、成本性质和对论文的影响，"
            "由组长确认后才能另建模型版本；当前数据不作修改。",
            status="unmodelled_business_rule",
        )
    if not api_key and client is None:
        raise AgentRuntimeError("尚未配置模型API Key，当前只有确定性核算工具可以离线运行")
    if not base_url and client is None:
        raise AgentRuntimeError("尚未配置百炼北京地域的API Base URL")
    if client is None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise AgentRuntimeError("缺少openai依赖，请先安装requirements.txt") from exc
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=35, max_retries=0)

    clean_history: list[dict[str, str]] = []
    for msg in (history or [])[-8:]:
        if (isinstance(msg, dict) and msg.get("role") in {"user", "assistant"}
                and isinstance(msg.get("content"), str)):
            clean_history.append({"role": msg["role"], "content": msg["content"][:1600]})
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_INSTRUCTION},
        *clean_history,
        {"role": "user", "content": question},
    ]
    records: list[dict[str, Any]] = []
    seen_calls: set[str] = set()
    calls_used = 0

    for _ in range(MAX_TOOL_CALLS + 1):
        try:
            request: dict[str, Any] = {
                "model": model, "messages": messages, "tools": TOOL_DEFINITIONS,
                "tool_choice": "auto", "max_tokens": 1500,
            }
            # 百炼文档：Qwen3.7 Flash 默认开启思考。此次任务由Python做
            # 精确计算，模型负责意图识别；关闭默认思考可减少不必要的推理token
            # 和截断风险。此参数为百炼扩展项，不能不加区别地传给其它厂商。
            if model.lower().startswith(("qwen3.7-", "qwen3.6-", "qwen3.8-")):
                request["extra_body"] = {"enable_thinking": False}
            answer = client.chat.completions.create(**request)
            choice = answer.choices[0]
            if _get_message_field(choice, "finish_reason") == "length":
                raise AgentRuntimeError("模型输出触及单轮长度上限，可能存在未完成的工具调用或结论；请缩短问题")
            msg = choice.message
        except AgentRuntimeError:
            raise
        except Exception as exc:
            raise AgentRuntimeError(f"模型API调用失败（{type(exc).__name__}）：请检查密钥、地域、额度与网络") from exc

        tool_calls = _get_message_field(msg, "tool_calls") or []
        if not tool_calls:
            content = _get_message_field(msg, "content") or ""
            evidence_records = [
                r for r in records if r["name"] == "lookup_case_evidence" and r["status"] == "ok"
            ]
            if _requires_evidence_tool(question) and evidence_records and not any(
                r["result"]["matched"] for r in evidence_records
            ):
                return AgentTurn(
                    "既有交接资料没有找到与本问题相匹配的中车四方企业事实证据。不能将作者教学假设或模型结果说成企业实际记录。",
                    records, model, "no_case_evidence",
                )
            successful = {r["name"] for r in records if r["status"] == "ok"}
            numeric_successful = successful & {
                "calculate_t0_abc", "optimize_t1", "compare_t1_scenarios",
            }
            if _is_fact_only_question(question) and numeric_successful:
                return AgentTurn(
                    "本轮询问的是公开企业事实，模型却调用了作者的教学模拟计算。"
                    "为避免混淆企业披露与虚构X/Y金额，已撤回该计算结论；请重新进行企业资料查询。",
                    records, model, "invalid_fact_simulation_route",
                )
            if _real_enterprise_amount_request(question) and successful & {
                "calculate_t0_abc", "optimize_t1", "compare_t1_scenarios",
            }:
                return AgentTurn(
                    "本问题要求核验真实企业财务数据，不能以X/Y教学情景的核算结果代替。"
                    "请查询正式企业来源；目前工具没有企业内部真实台账。",
                    records, model, "invalid_fact_simulation_route",
                )
            if _real_enterprise_amount_request(question) and evidence_records:
                return AgentTurn(
                    "当前经批准的企业资料条目仅是同一篇2023年报道的管理会计实践摘要，"
                    "没有可核验的中车四方真实订单、产品单位成本或利润金额。"
                    "即使检索命中相关主题，也不能把该报道摘要当成实际财务数据来源；"
                    "请另行取得明确的原始披露并经组长审查后才能给出真实数字。",
                    records, model, "real_enterprise_amount_not_available",
                )
            has_t0 = "calculate_t0_abc" in successful
            has_t1 = bool(successful & {"optimize_t1", "compare_t1_scenarios"})
            numeric_required = _requires_numeric_tool(question)
            require_t0 = numeric_required and bool(re.search(
                r"T0|ABC|作业成本法|成本池|准备作业|检验支持|分摊", question, re.I))
            require_t1 = numeric_required and bool(re.search(
                r"T1|最优|整数优化|需求|产能|瓶颈|调价|降价|工时|"
                r"[XY].{0,12}(?:售价|价格|变动支出|变动成本)|"
                r"(?:售价|价格).{0,8}[XY]", question, re.I))
            if numeric_required and ((require_t0 is True and require_t1 is False and has_t1)
                                    or (require_t1 is True and require_t0 is False and has_t0)):
                return AgentTurn(
                    "模型同时调用了与当前问题不对应的教学期间工具，已停止混合展示T0/T1结果。"
                    "请将历史ABC和独立T1优化分开询问，或者明确要求同时核验两域。",
                    records, model, "unverified_numeric_route",
                )
            requires_t1_comparison = (require_t1 and not require_t0 and bool(re.search(
                r"如何变化|有何变化|变化多少|变化情况|变化前后|相比|比较|对比|区别|差异|差额|增减|反转",
                question,
            )))
            if numeric_required and not _real_enterprise_amount_request(question) and not (
                (has_t0 or has_t1) and (not require_t0 or has_t0)
                and (not require_t1 or has_t1)
                and (not requires_t1_comparison or "compare_t1_scenarios" in successful)
            ):
                return AgentTurn(
                    "本轮模型没有成功调用与提问所需T0/T1域一致的确定性核算工具，"
                    "因此不能可靠给出数值结论。请使用‘计算器模式’计算，或明确所需T0/T1场景。",
                    records, model, "unverified_numeric_route",
                )
            if _requires_evidence_tool(question) and not evidence_records:
                return AgentTurn(
                    "涉及真实企业做法，但本轮未成功执行既有资料检索，无法核验事实。请明确要查询的公开实践；智能体模式仍可调用既有的公开资料检索工具。",
                    records, model, "unverified_evidence_route",
                )
            param_mismatch = verify_interpreted_parameters(question, records)
            if param_mismatch:
                return AgentTurn(param_mismatch + "请重新明确参数或改用表单手动输入。",
                                 records, model, "intent_parameter_mismatch")
            if successful:
                # 重要：只要本轮涉及正式计算或企业来源，最终可展示的结论一律由工具记录
                # 确定性构造；模型即使调用了工具，也可能在自由生成的文本中写错数字。
                return AgentTurn(verified_tool_answer(records), records, model, "verified_tool_output")
            return AgentTurn(content.strip() or "模型未返回可显示的说明，请重试。", records, model)

        if calls_used + len(tool_calls) > MAX_TOOL_CALLS:
            raise AgentRuntimeError("模型工具调用次数达到上限；已阻止可能的重复循环")
        # 仅回传兼容协议的必要字段，避免SDK对象携带其他平台不认识的扩展字段。
        normalized_calls = []
        for call in tool_calls:
            function = _get_message_field(call, "function")
            call_id = _get_message_field(call, "id")
            name = _get_message_field(function, "name")
            raw = _get_message_field(function, "arguments")
            if not (isinstance(call_id, str) and call_id and isinstance(name, str)
                    and isinstance(raw, str)):
                raise AgentRuntimeError("模型返回不完整的工具调用标识/函数信息，已停止，未进行伪造补全")
            normalized_calls.append({"id": call_id, "type": "function",
                                     "function": {"name": name, "arguments": raw}})
        if len({call["id"] for call in normalized_calls}) != len(normalized_calls):
            raise AgentRuntimeError("模型在同一响应中使用了重复的tool_call_id；已停止")
        messages.append({"role": "assistant", "content": _get_message_field(msg, "content"),
                         "tool_calls": normalized_calls})
        for call in tool_calls:
            function = _get_message_field(call, "function")
            name = _get_message_field(function, "name")
            raw = _get_message_field(function, "arguments") or "{}"
            call_id = _get_message_field(call, "id")
            try:
                args = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(
                    AgentInputError(f"工具JSON含非法常量{value}")
                ), object_pairs_hook=_no_duplicate_keys)
                if name == "lookup_case_evidence" and isinstance(args, dict) and set(args) == {"query"}:
                    # 企业事实检索必须针对用户真实问题，不能仅凭模型自由改写
                    # 一个无关查询词来“命中”合法来源后答非所问。
                    args = {"query": question}
                signature = json.dumps({"name": name, "arguments": args}, sort_keys=True, ensure_ascii=False)
                if signature in seen_calls:
                    raise AgentInputError("模型试图重复执行同一工具与参数")
                seen_calls.add(signature)
                result = execute_tool(name, args)
                records.append({"name": name, "arguments": args, "status": "ok", "result": result})
                output = {"status": "ok", "result": result}
            except (AgentInputError, ENGINE.ModelInputError, ValueError, TypeError) as exc:
                records.append({"name": name, "status": "error", "error": str(exc)})
                output = {"status": "error", "message": str(exc)}
            messages.append({"role": "tool", "tool_call_id": call_id,
                             "content": json.dumps(output, ensure_ascii=False, allow_nan=False)})
            calls_used += 1
        if any(r["status"] == "error" for r in records):
            errors = [r for r in records if r["status"] == "error"]
            return AgentTurn(
                "确定性工具未通过输入校验，已停止生成数值结论："
                + "；".join(f"{r['name']}：{r['error']}" for r in errors)
                + "。请更正问题或参数再试。",
                records, model, "tool_input_error",
            )

    raise AgentRuntimeError("模型在规定次数内仍未结束工具调用；已停止避免费用失控")
