"""中车四方管理会计教学决策辅助智能体：Streamlit网页开发版。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from agent_core import (
    AgentInputError, AgentRuntimeError, calculate_t0, calculate_t1,
    compare_t1, get_identity, load_frozen_data, run_agent,
)


st.set_page_config(
    page_title="中车四方 · 管理会计决策辅助智能体",
    page_icon="📊",
    layout="wide",
)

# 仅加载外观样式：不创建新控件，不改动任何计算、模型或会话逻辑。
st.html(Path(__file__).resolve().parent / "styles" / "visual.css")


def _setting(name: str, fallback: str = "") -> str:
    value = os.getenv(name)
    if value:
        return str(value).strip()
    try:
        return str(st.secrets.get(name, fallback)).strip()
    except (FileNotFoundError, KeyError):
        return fallback


def _looks_like_configured_api(key: str, base: str) -> bool:
    """这里只筛查明显占位符；验证服务有效性仍必须进行真实API调用。"""
    forbidden = ("请替换", "你的", "YOUR", "<", ">", "{", "}", "示例")
    return bool(key and len(key) > 8 and base.startswith("https://")
                and "/compatible-mode/v1" in base
                and not any(token in key or token in base for token in forbidden))


def _record(category: str, value: dict) -> None:
    st.session_state.setdefault("trace", []).append({
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "category": category,
        "value": value,
    })
    st.session_state["trace"] = st.session_state["trace"][-100:]


def _money(value: int | float) -> str:
    return f"¥{value:,.2f}"


def _render_t1(payload: dict) -> None:
    outcome = payload["result"]
    if outcome["status"] != "optimal":
        st.error(f"本情景无可行方案：{outcome.get('reason', '约束不相容')}")
        return
    a, b, c = st.columns(3)
    a.metric("X最优件数", outcome["x"])
    b.metric("Y最优件数", outcome["y"])
    c.metric("最优总边际贡献", _money(outcome["total_contribution"]))
    st.write(
        f"简化期间余额：{_money(outcome['simplified_period_residual'])}；"
        f"已使用瓶颈工时：{outcome['capacity_used_hours']}小时；"
        f"剩余工时：{outcome['capacity_slack_hours']}小时。"
    )
    inputs = payload["effective_inputs"]
    cm = outcome["unit_contribution"]
    st.write(
        "**会计决策依据**：最大化相关边际贡献 "
        f"Z=({_money(cm['X'])})×x+({_money(cm['Y'])})×y；"
        f"工时约束为{inputs['bottleneck_processing_hours_per_unit']['X']}x"
        f"+{inputs['bottleneck_processing_hours_per_unit']['Y']}y≤{inputs['total_capacity_hours']}小时；"
        f"数量是整数，X/Y需求上限为{inputs['maximum_quantity']['X']}/"
        f"{inputs['maximum_quantity']['Y']}件。"
    )
    if outcome["optimal_solution_count"] > 1:
        st.warning(
            f"注意：存在{outcome['optimal_solution_count']}个并列最优解，"
            "展示的X/Y组合只是一种，不可称为唯一解。"
        )
    with st.expander("查看输入、公式核算和原始工具输出"):
        st.markdown("**本次真正参与计算的全部参数（含沿用基准的默认值）**")
        st.json(payload["effective_inputs"])
        st.markdown("**原始求解记录**")
        st.json(payload)
    st.caption("T1为独立教学期间：已承诺固定支持费用仅在简化期间余额中扣除一次，不改变最优排序。")


identity = get_identity()
data = load_frozen_data()
key = _setting("DASHSCOPE_API_KEY")
base_url = _setting("DASHSCOPE_BASE_URL")
model = _setting("QWEN_MODEL", "qwen3.7-flash")
api_ready = _looks_like_configured_api(key, base_url)

st.title("中车四方 · 管理会计决策辅助智能体")
st.caption(
    "大学管理会计案例的作者教学模拟工具｜"
    f"计算基线：{identity['baseline_version']}｜"
    f"智能体工程：{identity['agent_version']}"
)
st.warning(
    "研究企业为中车青岛四方机车车辆股份有限公司；X/Y产品、售价、成本和工时均属教学假设。",
    icon="📌",
)

with st.sidebar:
    st.header("运行状态")
    if api_ready:
        st.success(f"已读取模型服务配置：{model}")
    else:
        st.info("尚未配置千问API：可离线验证数学内核；自然语言智能体功能暂不可用。")
    st.write(f"基线版本：`{identity['baseline_version']}`")
    if st.button("清空当前会话", use_container_width=True):
        st.session_state["chat"] = []
        st.session_state["trace"] = []
        st.session_state.pop("last_t0", None)
        st.session_state.pop("last_t1", None)
        st.rerun()


chat_tab, calc_tab, audit_tab = st.tabs([
    "🤖 智能体模式", "📐 计算器模式", "🧪 开发者模式"
])

with chat_tab:
    with st.container():
        st.caption("建议尝试：‘计算T0两阶段ABC’、‘H300时最优方案’、‘Y降价到1700后的变化’。")
        for msg in st.session_state.setdefault("chat", []):
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                if msg.get("tool_records"):
                    with st.expander("本轮真实工具调用记录"):
                        st.json(msg["tool_records"])

        question = st.chat_input("请输入管理会计问题、动态情景或企业公开实践问题")
        if question:
            st.session_state["chat"].append({"role": "user", "content": question})
            with st.chat_message("user"):
                st.markdown(question)
            with st.chat_message("assistant"):
                if not api_ready:
                    response = "目前未配置千问API，无法提供真实的自然语言智能体调用。请先在‘计算器模式’体验已通过测试的确定性工具。"
                    records: list[dict] = []
                    turn_status = "api_not_configured"
                else:
                    previous = [
                        {"role": m["role"], "content": m["content"]}
                        for m in st.session_state["chat"][:-1]
                        if m["role"] in ("user", "assistant")
                    ]
                    try:
                        with st.spinner("模型理解问题、校验并执行必要的工具……"):
                            result = run_agent(
                                question,
                                api_key=key,
                                base_url=base_url,
                                model=model,
                                history=previous,
                            )
                        response, records, turn_status = result.answer, result.tool_records, result.status
                    except (AgentInputError, AgentRuntimeError) as exc:
                        response, records = f"本轮未完成：{exc}", []
                        turn_status = "runtime_error"
                    except Exception:
                        response, records = "本轮遇到未预期的技术异常，已停止输出结论。请保存提问文本并报告开发者进行复现和排查。", []
                        turn_status = "unexpected_error"
                st.markdown(response)
                if records:
                    with st.expander("本轮真实工具调用记录"):
                        st.json(records)
            st.session_state["chat"].append({"role": "assistant", "content": response,
                                             "tool_records": records})
            _record("chat_turn", {"question": question, "answer": response,
                                  "tools": records, "status": turn_status,
                                  "model": model if api_ready else None})

with calc_tab:
    st.subheader("确定性计算：T0 / T1 / 动态情景")
    if st.button("计算并核查T0两阶段ABC", type="secondary"):
        st.session_state.pop("last_t0", None)
        try:
            st.session_state["last_t0"] = calculate_t0()
            _record("t0_calculated", st.session_state["last_t0"])
        except Exception as exc:
            st.error(f"T0运算失败：{exc}")
    if "last_t0" in st.session_state:
        t0 = st.session_state["last_t0"]
        cols = st.columns(4)
        cols[0].metric("准备作业成本池", _money(t0["result"]["pools"]["preparation"]))
        cols[1].metric("检验支持成本池", _money(t0["result"]["pools"]["inspection_support"]))
        cols[2].metric("X分摊", _money(t0["result"]["allocated_support_cost"]["X"]))
        cols[3].metric("Y分摊", _money(t0["result"]["allocated_support_cost"]["Y"]))
        st.json(t0)
        st.caption("注意：X为530元、Y为−30元的是自定义单位内部分析余额，非真实财务利润。")

    st.divider()
    st.subheader("T1：独立教学期间的情景参数")
    with st.form("t1_form"):
        l, r = st.columns(2)
        with l:
            capacity = st.number_input("总瓶颈加工能力H（小时）", min_value=0.0, max_value=100000.0,
                                       value=float(data["T1"]["base_total_bottleneck_hours"]), step=0.5)
            px = st.number_input("X售价（元/件）", min_value=0.0,
                                 value=float(data["T1"]["price_per_unit_base"]["X"]), step=50.0)
            py = st.number_input("Y售价（元/件）", min_value=0.0,
                                 value=float(data["T1"]["price_per_unit_base"]["Y"]), step=50.0)
            max_x = st.number_input("X需求上限（件）", min_value=0, max_value=1000,
                                    value=data["T1"]["maximum_quantity"]["X"], step=1)
        with r:
            max_y = st.number_input("Y需求上限（件）", min_value=0, max_value=1000,
                                    value=data["T1"]["maximum_quantity"]["Y"], step=1)
            vx = st.number_input("X综合相关变动支出（元/件）", min_value=0.0,
                                 value=float(data["T1"]["avoidable_variable_outlay_per_unit"]["X"]), step=50.0)
            vy = st.number_input("Y综合相关变动支出（元/件）", min_value=0.0,
                                 value=float(data["T1"]["avoidable_variable_outlay_per_unit"]["Y"]), step=50.0)
        with st.expander("高级情景参数：既有模型允许的最低件数、单件工时及额外固定费用"):
            st.caption("这里只覆盖T1本轮情景；不会写入冻结的v2.2教学JSON，也不会改T0历史ABC。")
            u, v = st.columns(2)
            with u:
                min_x = st.number_input("X最低生产数量（件）", min_value=0, max_value=1000,
                                        value=data["T1"]["minimum_quantity"]["X"], step=1)
                hour_x = st.number_input("X单件瓶颈加工工时（小时/件）", min_value=0.01,
                                         value=float(data["T1"]["bottleneck_processing_hours_per_unit"]["X"]), step=0.25)
            with v:
                min_y = st.number_input("Y最低生产数量（件）", min_value=0, max_value=1000,
                                        value=data["T1"]["minimum_quantity"]["Y"], step=1)
                hour_y = st.number_input("Y单件瓶颈加工工时（小时/件）", min_value=0.01,
                                         value=float(data["T1"]["bottleneck_processing_hours_per_unit"]["Y"]), step=0.25)
            extra_fixed = st.number_input("本情景新增固定相关支出总额（元，非每件）", min_value=0.0,
                                          value=0.0, step=100.0)
            st.caption("新增固定支出是假设整个情景都会发生的总额；不代表分段加班或达到一定产量才发生的阶梯成本。")
        submitted = st.form_submit_button("重新求最优并与H300标准情景比较", type="primary")
    if submitted:
        overrides = {
            "total_capacity_hours": capacity,
            "price_per_unit": {"X": px, "Y": py},
            "avoidable_variable_outlay_per_unit": {"X": vx, "Y": vy},
            "maximum_quantity": {"X": int(max_x), "Y": int(max_y)},
            "minimum_quantity": {"X": int(min_x), "Y": int(min_y)},
            "bottleneck_processing_hours_per_unit": {"X": hour_x, "Y": hour_y},
            "additional_fixed_outlay_for_scenario": extra_fixed,
        }
        # 关键：一旦提交新问题，先撤下旧结果；即使新输入无效，也不能
        # 在红色报错之下继续显示上次的X/Y最优件数并被误认为新答案。
        st.session_state.pop("last_t1", None)
        try:
            st.session_state["last_t1"] = compare_t1(overrides)
            _record("t1_compared", st.session_state["last_t1"])
        except (AgentInputError, ValueError) as exc:
            st.error(f"情景无效：{exc}")
    if "last_t1" in st.session_state:
        outcome = st.session_state["last_t1"]
        _render_t1(outcome["modified"])
        if outcome["difference"] is not None:
            d = outcome["difference"]
            st.write(
                f"**对比冻结T1基准（H300，X75/Y100）**："
                f"X改变{d['x_change']:+d}件、Y改变{d['y_change']:+d}件，"
                f"总边际贡献改变{_money(d['contribution_change'])}，"
                f"简化期间余额改变{_money(d['period_residual_change'])}。"
            )
            st.caption(
                "差值由两次独立求解产生，不是用T0的历史ABC分摊额直接加减；"
                "即使单件贡献率看似更高，瓶颈工时、上下限及整数性仍会共同影响组合。"
            )
            with st.expander("相较于冻结H300标准情景的变化"):
                st.json(outcome["difference"])

with audit_tab:
    st.subheader("版本、基线与运行留痕")
    st.json(identity)
    if st.button("执行3个教学标准情景复核"):
        try:
            ENGINE_ANSWER = []
            for scenario in data["T1"]["scenarios"]:
                result = calculate_t1({
                    "total_capacity_hours": scenario["total_capacity_hours"],
                    "price_per_unit": scenario["price_per_unit"],
                })["result"]
                actual = (result.get("x"), result.get("y"),
                          result.get("total_contribution"), result.get("simplified_period_residual"))
                expected = (scenario["expected_optimal_quantity"]["X"],
                            scenario["expected_optimal_quantity"]["Y"],
                            scenario["expected_total_contribution"],
                            scenario["expected_simplified_period_residual"])
                if result["status"] != "optimal" or actual != expected:
                    raise AssertionError(f"标准情景{scenario['id']}复核失败：实算{actual} ≠ 约定{expected}")
                ENGINE_ANSWER.append({"id": scenario["id"], "result": result,
                                      "expected": scenario["expected_optimal_quantity"]})
            st.success("三个标准情景均已重算，并逐一与约定件数、边际贡献及期间余额比较一致。")
            st.json(ENGINE_ANSWER)
            _record("standard_scenarios_checked", {"results": ENGINE_ANSWER})
        except Exception as exc:
            st.error(f"校验失败：{exc}")
    log = st.session_state.get("trace", [])
    st.caption("此处日志只保存当前会话的有限记录。云端重启后可能消失；请下载保存重要验收证据。")
    st.download_button(
        "下载本会话测试与工具调用记录（JSON）",
        data=json.dumps(log, ensure_ascii=False, indent=2),
        file_name="agent_session_audit.json",
        mime="application/json",
        disabled=not log,
    )
