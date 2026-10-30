"""中车四方教学情景：可审计核算/优化参考内核 v2.2。非企业实账，非LLM智能体。

使用：python 中车四方_确定性核算内核_v2_2.py --data 中车四方_教学参数_v2_2.json
核心原则：精确十进制计算、输入严格校验、T0/T1隔离、未知情景条件拒绝静默忽略。
"""
from __future__ import annotations

import argparse
import json
import math
import hashlib
from decimal import Decimal
from pathlib import Path
from typing import Any

PRODUCTS = ('X', 'Y')
ACTIVITIES = ('preparation', 'inspection_support')
MAX_ENUMERATION_CANDIDATES = 250_000
T0_ALLOWED_KEYS = frozenset({
 'period','quantity','price_per_unit','avoidable_variable_outlay_per_unit',
 'fixed_resource_costs','resource_usage','activity_occurrences',
 'bottleneck_processing_hours_per_unit','expected','data_character'
})
T1_ALLOWED_KEYS = frozenset({
 'period','no_external_base_business','no_preexisting_mandatory_orders',
 'production_equals_sales','fixed_support_cost_independent_period',
 'price_per_unit_base','avoidable_variable_outlay_per_unit',
 'bottleneck_processing_hours_per_unit','minimum_quantity','maximum_quantity',
 'integer_quantities','base_total_bottleneck_hours','price_sensitivity_keeps_demand_fixed',
 'scenarios','data_character','dynamic_overrides_supported','scenario_extra_cost_rule',
 'base_restored_350_scenario_extra_cost_is_zero','model_convention'
})
SCENARIO_ALLOWED_KEYS = frozenset({
    'id','name','total_capacity_hours','price_per_unit',
    'avoidable_variable_outlay_per_unit','minimum_quantity','maximum_quantity',
    'bottleneck_processing_hours_per_unit','additional_fixed_outlay_for_scenario',
    'expected_optimal_quantity','expected_total_contribution','expected_simplified_period_residual'
})

class ModelInputError(ValueError):
    """输入不符合明确的教学模型合同。"""


def number(value: Any, path: str, *, allow_zero: bool = True) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ModelInputError(f'{path}必须是有限的数值，不能使用布尔值、文字或缺失值')
    if isinstance(value, float) and not math.isfinite(value):
        raise ModelInputError(f'{path}必须为有限数值，不能是NaN或无穷大')
    x = Decimal(str(value))
    if not x.is_finite() or x < 0 or x > Decimal('1e12') or (not allow_zero and x == 0):
        raise ModelInputError(f'{path}必须为不超过10^12的{"正数" if not allow_zero else "非负数"}')
    return x


def money(value: Any, path: str, *, allow_zero: bool = True) -> Decimal:
    d = number(value, path, allow_zero=allow_zero)
    if d.as_tuple().exponent < -2 and d != d.quantize(Decimal('0.01')):
        raise ModelInputError(f'{path}以人民币元计，不接受超过两位小数的金额')
    return d


def natural(value: Any, path: str, *, strictly_positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ModelInputError(f'{path}必须是整数件数/次数，不能是小数、布尔值或文字')
    if value > 1_000_000:
        raise ModelInputError(f'{path}超过本教学内核允许的100万件/次上限')
    if value < (1 if strictly_positive else 0):
        raise ModelInputError(f'{path}必须为{"正整数" if strictly_positive else "非负整数"}')
    return value


def exactly_keys(mapping: Any, expected: set[str], path: str) -> dict:
    if not isinstance(mapping, dict):
        raise ModelInputError(f'{path}必须是对象/字典')
    got = set(mapping)
    if got != expected:
        raise ModelInputError(f'{path}字段不一致：缺少{sorted(expected-got)}，不支持{sorted(got-expected)}')
    return mapping


def product_map(data: Any, path: str, validator) -> dict:
    mapping = exactly_keys(data, set(PRODUCTS), path)
    return {p: validator(mapping[p], f'{path}.{p}') for p in PRODUCTS}


def presentation(d: Decimal | int) -> float | int:
    if isinstance(d, int):
        return d
    return int(d) if d == d.to_integral_value() else float(d)


def verify_t0(data: dict) -> dict:
    """按输入独立核算，不在计算函数内硬编码任何测试答案。"""
    try:
        t = data['T0']
        unknown = set(t)-T0_ALLOWED_KEYS
        if unknown: raise ModelInputError(f'T0出现未建模字段{sorted(unknown)}，拒绝静默忽略')
        rc = exactly_keys(t['fixed_resource_costs'], {'staff_support','equipment_depreciation_support'}, 'T0.fixed_resource_costs')
        costs = {k: money(v, f'T0.fixed_resource_costs.{k}') for k,v in rc.items()}
        usage = exactly_keys(t['resource_usage'], {'staff_hours','support_equipment_hours'}, 'T0.resource_usage')
        use = {r: exactly_keys(usage[r], set(ACTIVITIES), f'T0.resource_usage.{r}') for r in usage}
        use = {r: {k: number(v, f'T0.resource_usage.{r}.{k}') for k,v in use[r].items()} for r in use}
        activity = exactly_keys(t['activity_occurrences'], set(ACTIVITIES), 'T0.activity_occurrences')
        act = {k: product_map(activity[k], f'T0.activity_occurrences.{k}', natural) for k in ACTIVITIES}
        qty = product_map(t['quantity'], 'T0.quantity', lambda v,p: natural(v,p,strictly_positive=True))
        price = product_map(t['price_per_unit'], 'T0.price_per_unit', money)
        outlay = product_map(t['avoidable_variable_outlay_per_unit'], 'T0.avoidable_variable_outlay_per_unit', money)
        bottleneck = product_map(t['bottleneck_processing_hours_per_unit'], 'T0.bottleneck_processing_hours_per_unit', lambda v,p: number(v,p,allow_zero=False))
    except KeyError as e:
        raise ModelInputError(f'T0缺少必要字段：{e}') from e

    staff_total = sum(use['staff_hours'].values())
    equip_total = sum(use['support_equipment_hours'].values())
    if staff_total <= 0 or equip_total <= 0:
        raise ModelInputError('T0支持人员工时与支持设备工时各自总量必须大于零')
    for k in ACTIVITIES:
        if sum(act[k].values()) <= 0:
            raise ModelInputError(f'T0.{k}动因次数总量必须大于零')

    staff_rate = costs['staff_support']/staff_total
    equip_rate = costs['equipment_depreciation_support']/equip_total
    pools = {k: use['staff_hours'][k]*staff_rate + use['support_equipment_hours'][k]*equip_rate for k in ACTIVITIES}
    rates = {k: pools[k] / sum(act[k].values()) for k in ACTIVITIES}
    allocated = {p: sum((rates[k]*act[k][p] for k in ACTIVITIES), Decimal('0')) for p in PRODUCTS}
    if abs(sum(pools.values()) - sum(costs.values())) > Decimal('0.000001') or abs(sum(allocated.values()) - sum(pools.values())) > Decimal('0.000001'):
        raise ArithmeticError('T0成本归集未满足费用守恒')
    unit_alloc = {p: allocated[p]/qty[p] for p in PRODUCTS}
    contribution = {p: price[p] - outlay[p] for p in PRODUCTS}
    residual = {p: contribution[p] - unit_alloc[p] for p in PRODUCTS}
    total_contribution = sum((qty[p]*contribution[p] for p in PRODUCTS), Decimal('0'))
    total_period_residual = total_contribution - sum(costs.values())
    qty_total = sum(qty.values())
    proc_total = sum((bottleneck[p]*qty[p] for p in PRODUCTS), Decimal('0'))
    unit_by_quantity = {p: sum(costs.values())/qty_total for p in PRODUCTS}
    unit_by_hours = {p: sum(costs.values())/proc_total*bottleneck[p] for p in PRODUCTS}
    return {
        'resource_rates': {'staff_hour':presentation(staff_rate), 'support_equipment_hour':presentation(equip_rate)},
        'pools':{k:presentation(v) for k,v in pools.items()},
        'activity_rates':{k:presentation(v) for k,v in rates.items()},
        'allocated_support_cost':{p:presentation(v) for p,v in allocated.items()},
        'allocated_unit_support_cost':{p:presentation(v) for p,v in unit_alloc.items()},
        'unit_internal_residual':{p:presentation(v) for p,v in residual.items()},
        'aggregate_contribution':presentation(total_contribution),
        'simplified_period_residual':presentation(total_period_residual),
        'unit_allocation_by_quantity':{p:presentation(v) for p,v in unit_by_quantity.items()},
        'unit_allocation_by_processing_hours':{p:presentation(v) for p,v in unit_by_hours.items()},
        'note':'T0独立期间的归集计算；并非企业实账，分摊额不表示可避免现金。'
    }


def optimize_t1(data: dict, scenario: dict) -> dict:
    """两种教学产品的有界整数穷举；严格拒绝未建模字段，不暗中忽略费用/约束。"""
    if not isinstance(scenario, dict):
        raise ModelInputError('T1情景必须是字典')
    unknown = set(scenario)-SCENARIO_ALLOWED_KEYS
    if unknown:
        raise ModelInputError(f'T1包含未建模字段{sorted(unknown)}，不能静默忽略；需扩展模型或移除')
    if not {'total_capacity_hours', 'price_per_unit'} <= set(scenario):
        raise ModelInputError('T1情景必须包括total_capacity_hours、price_per_unit')
    assumptions = {
        'no_external_base_business': '当前模型不支持模型外基准业务',
        'no_preexisting_mandatory_orders': '当前模型不支持已签强制订单，请先显式建模',
        'production_equals_sales': '当前模型不支持跨期库存/产销不一致',
        'integer_quantities': '当前模型只支持整数件数',
    }
    for key, message in assumptions.items():
        if data.get('T1', {}).get(key) is not True:
            raise ModelInputError(f'T1.{key}未满足：{message}')
    t_keys = set(data['T1'])
    if t_keys - T1_ALLOWED_KEYS:
        raise ModelInputError(f'T1存在未知业务字段{sorted(t_keys-T1_ALLOWED_KEYS)}，拒绝静默忽略')
    if data['T1'].get('price_sensitivity_keeps_demand_fixed') is False:
        changed_price = scenario['price_per_unit'] != data['T1'].get('price_per_unit_base')
        if changed_price and 'maximum_quantity' not in scenario:
            raise ModelInputError('售价变化会影响需求时必须显式提供新的maximum_quantity，不能直接沿用原上限')
    try:
        t = data['T1']
        H = number(scenario['total_capacity_hours'], 'T1.total_capacity_hours')
        price = product_map(scenario['price_per_unit'], 'T1.price_per_unit', money)
        var_raw = scenario.get('avoidable_variable_outlay_per_unit',t['avoidable_variable_outlay_per_unit'])
        var = product_map(var_raw,'T1.avoidable_variable_outlay_per_unit',money)
        unit_raw = scenario.get('bottleneck_processing_hours_per_unit',t['bottleneck_processing_hours_per_unit'])
        unit = product_map(unit_raw,'T1.bottleneck_processing_hours_per_unit',lambda v,p:number(v,p,allow_zero=False))
        lower_raw = scenario.get('minimum_quantity',t['minimum_quantity'])
        upper_raw = scenario.get('maximum_quantity',t['maximum_quantity'])
        mins = product_map(lower_raw,'T1.minimum_quantity',natural)
        maxs = product_map(upper_raw,'T1.maximum_quantity',natural)
        fixed = money(t['fixed_support_cost_independent_period'],'T1.fixed_support_cost_independent_period')
        additional = money(scenario.get('additional_fixed_outlay_for_scenario',0), 'T1.additional_fixed_outlay_for_scenario')
    except KeyError as e:
        raise ModelInputError(f'T1缺少必要字段：{e}') from e
    if any(mins[p] > maxs[p] for p in PRODUCTS):
        raise ModelInputError('T1数量下限不得超过上限')
    count = (maxs['X']-mins['X']+1)*(maxs['Y']-mins['Y']+1)
    if count > MAX_ENUMERATION_CANDIDATES:
        raise ModelInputError(f'候选组合{count}超过穷举安全上限{MAX_ENUMERATION_CANDIDATES}；请改用整数规划求解器')

    cm = {p: price[p] - var[p] for p in PRODUCTS}
    best = None
    best_solutions = []
    solution_count = 0
    for x in range(mins['X'], maxs['X']+1):
        for y in range(mins['Y'], maxs['Y']+1):
            hours = x*unit['X']+y*unit['Y']
            if hours > H:
                continue
            z = x*cm['X']+y*cm['Y']
            if best is None or z > best:
                best = z
                solution_count = 1
                best_solutions = [(x,y)]
            elif z == best:
                solution_count += 1
                if len(best_solutions)<30: best_solutions.append((x,y))
    if best is None:
        return {'status':'infeasible','solutions':[], 'reason':'最低交付、最高需求和工时约束无法同时满足'}
    x,y=best_solutions[0]
    used = x*unit['X'] + y*unit['Y']
    return {
        'status':'optimal','x':x,'y':y,
        'total_contribution':presentation(best),
        'simplified_period_residual':presentation(best - fixed - additional),
        'capacity_used_hours':presentation(used),
        'capacity_slack_hours':presentation(H-used),
        'optimal_solution_count':solution_count,
        'optimal_solutions_sample':best_solutions,
        'unit_contribution':{p:presentation(cm[p]) for p in PRODUCTS},
        'support_fixed_outlay_for_period':presentation(fixed),
        'additional_fixed_outlay_for_scenario':presentation(additional),
        'note':'T1独立教学期间；优化基于相关边际贡献，不自动重算T0 ABC。额外固定成本为情景层面的独立总额。'
    }



BASELINE_INPUTS_SHA256 = '27941f3f7b2e6735063f1b12bddcf0123fedf5cf872581f4c6f0c6aeee673921'

# 本版基线仅用于模型开发交接。对解释性字段采用防意外串版指纹，
# 它是文件一致性措施，不是证明自然语言内容真实的签名或身份认证。
BASELINE_NOTES_SHA256 = '33fe9388fa6715b62f0648ce5b6d0c980baad539430a1f42620e47df302c3d13'

def validate_provenance_notes(data: dict) -> None:
    meta = data['meta']
    entity = data['entity']
    exactly_keys(meta, {'name','version','date','status','currency','source_type','usage','previous_version','schema_note'}, 'meta')
    exactly_keys(entity, {'name','evidence','fictional_product_labels'}, 'entity')
    if entity['name'] != '中车青岛四方机车车辆股份有限公司':
        raise ModelInputError('entity.name与当前实际研究主体不符；不得把同一教学数据无说明挪到其他公司')
    if not isinstance(data['cross_period_rules'], list) or not data['cross_period_rules']:
        raise ModelInputError('cross_period_rules不得为空')
    tests=data['validation_tests']
    if not isinstance(tests,list) or not tests:
        raise ModelInputError('validation_tests不得为空')
    ids=[]
    for i,item in enumerate(tests):
        exactly_keys(item, {'id','test','expect'}, f'validation_tests[{i}]')
        if not all(isinstance(item[k],str) and item[k].strip() for k in ('id','test','expect')):
            raise ModelInputError(f'validation_tests[{i}]存在空白描述')
        ids.append(item['id'])
    if len(ids)!=len(set(ids)):
        raise ModelInputError('validation_tests.id不允许重复')
    notes={
       'meta':{k:meta[k] for k in ['name','date','status','usage','schema_note','previous_version']},
       'entity':{k:entity[k] for k in ['name','evidence','fictional_product_labels']},
       'T0_data_character':data['T0'].get('data_character'),
       'T1_data_character':data['T1'].get('data_character'),
       'cross_period_rules':data['cross_period_rules'],
       'validation_tests':tests,
    }
    actual=hashlib.sha256(json.dumps(notes,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if actual != BASELINE_NOTES_SHA256:
        raise ModelInputError('受保护的交接身份/自然语言说明与v2.2基线不一致；请核查是否混入其他版本，勿静默通过')


def validate_dataset_contract(data: dict) -> None:
    """校验交接文件的声明层与基准情景是否一致；不会修改计算参数。

    与 verify_t0/optimize_t1 有意分层：用户合法的互动参数调整不应自动
    触发基准答案校验，只有导入新的标准基线时才进行整份文件的合同校验。
    """
    exactly_keys(data, {'meta', 'entity', 'T0', 'T1',
                        'cross_period_rules', 'validation_tests'}, 'root')
    meta = data['meta']
    if not isinstance(meta, dict) or meta.get('source_type') != 'author_assumption':
        raise ModelInputError('meta.source_type必须是author_assumption，不得把教学模型标成企业真实台账')
    if meta.get('currency') != 'CNY':
        raise ModelInputError('meta.currency必须为CNY；本模型金额单位是人民币元')
    if meta.get('version') != 'handoff-v2.2-audited':
        raise ModelInputError('数据版本不匹配，预期handoff-v2.2-audited')
    entity = data['entity']
    if not isinstance(entity, dict) or entity.get('fictional_product_labels') != ['X','Y']:
        raise ModelInputError('entity.fictional_product_labels必须是教学产品X、Y')
    validate_provenance_notes(data)
    t0, t1 = data['T0'], data['T1']
    exactly_keys(t0, set(T0_ALLOWED_KEYS), 'T0')
    exactly_keys(t1, set(T1_ALLOWED_KEYS), 'T1')
    if t0.get('period') != 'independent teaching period T0' or t1.get('period') != 'independent teaching period T1':
        raise ModelInputError('T0/T1教学期间标签不符合两期间独立的约定')
    if t1.get('price_sensitivity_keeps_demand_fixed') is not True:
        raise ModelInputError('基线假设售价变化但需求上限不变；修改须建立新完整数据版本')
    if t1.get('base_restored_350_scenario_extra_cost_is_zero') is not True:
        raise ModelInputError('基线恢复350小时情景的额外相关支出假设必须为0')
    base_hours = number(t1.get('base_total_bottleneck_hours'), 'T1.base_total_bottleneck_hours')
    scenarios = t1.get('scenarios')
    if not isinstance(scenarios, list) or not scenarios:
        raise ModelInputError('T1.scenarios必须为非空列表')
    ids = [s.get('id') if isinstance(s,dict) else None for s in scenarios]
    if any(not isinstance(i,str) or not i for i in ids) or len(ids)!=len(set(ids)):
        raise ModelInputError('T1场景id须为非空且不能重复')
    by_id = dict(zip(ids,scenarios))
    if set(by_id) != {'base_300','restored_350','Y_price_1700'}:
        raise ModelInputError('v2.2基线须恰好包含base_300、restored_350和Y_price_1700三个标准场景')
    baseline = by_id['base_300']
    if number(baseline.get('total_capacity_hours'), 'T1.scenarios.base_300.total_capacity_hours') != base_hours:
        raise ModelInputError('T1.base_total_bottleneck_hours与base_300场景的总产能不一致')
    if baseline.get('price_per_unit') != t1.get('price_per_unit_base'):
        raise ModelInputError('T1.price_per_unit_base与base_300场景价格不一致')
    restored = by_id['restored_350']
    if number(restored.get('total_capacity_hours'), 'T1.scenarios.restored_350.total_capacity_hours') != Decimal('350'):
        raise ModelInputError('restored_350应为350小时，不能仅因最优产量不变而接受其他产能')
    if restored.get('price_per_unit') != t1.get('price_per_unit_base'):
        raise ModelInputError('restored_350只调整产能，价格必须与基准一致')
    discount = by_id['Y_price_1700']
    if number(discount.get('total_capacity_hours'), 'T1.scenarios.Y_price_1700.total_capacity_hours') != base_hours:
        raise ModelInputError('Y_price_1700是单因素价格敏感性情景，产能必须与基准一致')
    if discount.get('price_per_unit') != {'X':t1['price_per_unit_base']['X'], 'Y':1700}:
        raise ModelInputError('Y_price_1700情景应保持X基准价、Y单价1700元')
    if number(restored.get('additional_fixed_outlay_for_scenario',0),
              'T1.scenarios.restored_350.additional_fixed_outlay_for_scenario') != 0:
        raise ModelInputError('restored_350的额外固定费用不为0，与当前声明矛盾')
    for scenario_id in ('base_300','Y_price_1700'):
        if number(by_id[scenario_id].get('additional_fixed_outlay_for_scenario',0),
                  f'T1.scenarios.{scenario_id}.additional_fixed_outlay_for_scenario') != 0:
            raise ModelInputError(f'{scenario_id}标准情景未设置额外固定相关支出，不能偷偷改动')
    supported = {
        'price_per_unit','avoidable_variable_outlay_per_unit','total_capacity_hours',
        'minimum_quantity','maximum_quantity','bottleneck_processing_hours_per_unit',
        'additional_fixed_outlay_for_scenario'
    }
    declared = t1.get('dynamic_overrides_supported')
    if not isinstance(declared,list) or len(declared)!=len(set(declared)) or set(declared)!=supported:
        raise ModelInputError('T1.dynamic_overrides_supported与实际支持的情景字段不一致')
    if not isinstance(data['cross_period_rules'], list) or not isinstance(data['validation_tests'],list):
        raise ModelInputError('cross_period_rules/validation_tests必须是列表')
    # 拒绝把成本/合同约束塞进仅用作标准答案的嵌套字段，防止假性覆盖。
    for scenario in scenarios:
        unknown = set(scenario)-SCENARIO_ALLOWED_KEYS
        if unknown:
            raise ModelInputError(f"标准场景{scenario.get('id')}含未知字段{sorted(unknown)}")
        exactly_keys(scenario['expected_optimal_quantity'], set(PRODUCTS),
                     f"T1.scenarios.{scenario['id']}.expected_optimal_quantity")
    expected_keys = {
        'staff_rate_per_hour','support_equipment_rate_per_hour','preparation_pool_cost',
        'inspection_support_pool_cost','preparation_rate_per_occurrence',
        'inspection_support_rate_per_occurrence','allocated_support_cost',
        'allocated_unit_support_cost','unit_internal_residual','aggregate_contribution',
        'simplified_period_residual','unit_allocation_by_quantity','unit_allocation_by_processing_hours'
    }
    exactly_keys(t0.get('expected'), expected_keys, 'T0.expected')
    # 计算结果相同不能证明输入参数没有被更改。所有结构化教学基准输入均予以冻结；
    # 这不妨碍optimize_t1在单独的用户情景中合法调整价格、工时或需求。
    input_payload={
        'T0':{k:v for k,v in t0.items() if k not in ('expected','data_character')},
        'T1':{k:v for k,v in t1.items() if k not in ('scenarios','data_character')},
        'scenarios':[{k:v for k,v in s.items() if not k.startswith('expected_')} for s in scenarios],
    }
    input_actual=hashlib.sha256(json.dumps(input_payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if input_actual != BASELINE_INPUTS_SHA256:
        raise ModelInputError('v2.2结构化教学基准输入或场景定义已更改：必须建立新版本，不能仅凭原结果相同宣告基线通过')


def validate_baseline_expectations(data: dict) -> None:
    validate_dataset_contract(data)
    got = verify_t0(data)
    exp = data['T0']['expected']
    key_map={
        'staff_rate_per_hour':got['resource_rates']['staff_hour'],
        'support_equipment_rate_per_hour':got['resource_rates']['support_equipment_hour'],
        'preparation_pool_cost':got['pools']['preparation'],
        'inspection_support_pool_cost':got['pools']['inspection_support'],
        'preparation_rate_per_occurrence':got['activity_rates']['preparation'],
        'inspection_support_rate_per_occurrence':got['activity_rates']['inspection_support'],
        'aggregate_contribution':got['aggregate_contribution'],
        'simplified_period_residual':got['simplified_period_residual']
    }
    for key, val in key_map.items():
        if Decimal(str(exp[key])) != Decimal(str(val)):
            raise AssertionError(f'T0 expected.{key}与重新计算结果不符：{exp[key]} vs {val}')
    check_maps={
        'allocated_support_cost':got['allocated_support_cost'],
        'allocated_unit_support_cost':got['allocated_unit_support_cost'],
        'unit_internal_residual':got['unit_internal_residual'],
        'unit_allocation_by_quantity':got['unit_allocation_by_quantity'],
        'unit_allocation_by_processing_hours':got['unit_allocation_by_processing_hours'],
    }
    for key, mapping in check_maps.items():
        for p in PRODUCTS:
            if abs(Decimal(str(exp[key][p]))-Decimal(str(mapping[p]))) > Decimal('0.000001'):
                raise AssertionError(f'T0 expected.{key}.{p}错误')
    for s in data['T1']['scenarios']:
        res=optimize_t1(data,s)
        if res['status'] != 'optimal': raise AssertionError(f"情景{s['id']}无可行解")
        actual=(res['x'],res['y'],res['total_contribution'],res['simplified_period_residual'])
        expected=(s['expected_optimal_quantity']['X'],s['expected_optimal_quantity']['Y'],s['expected_total_contribution'],s['expected_simplified_period_residual'])
        if actual!=expected: raise AssertionError(f"{s['id']}结果不符：{actual}≠{expected}")


def main() -> None:
    parser=argparse.ArgumentParser(description='可审计教学模型T0/T1核算器（须明确指定JSON路径）')
    parser.add_argument('--data',type=Path,required=True,help='数据JSON的明确完整或相对路径')
    args=parser.parse_args()
    path=args.data.expanduser().resolve(strict=True)
    def no_duplicate_keys(pairs):
        d={}
        for k,v in pairs:
            if k in d:
                raise ModelInputError(f'JSON不允许重复字段：{k}')
            d[k]=v
        return d
    with path.open('r',encoding='utf-8') as f:
        raw=json.load(
            f,
            parse_constant=lambda x:(_ for _ in ()).throw(ModelInputError(f'JSON禁止{x}')),
            object_pairs_hook=no_duplicate_keys,
        )
    print(f'实际加载参数文件：{path}')
    print(f"数据版本：{raw['meta']['version']}；身份：{raw['meta']['source_type']}")
    validate_baseline_expectations(raw)
    print('T0金额归集、基准标准值和T1标准情景均通过复核')
    print(json.dumps({'T0':verify_t0(raw)},ensure_ascii=False,indent=2))
    for sc in raw['T1']['scenarios']:
        print(json.dumps({'scenario':sc['id'],'result':optimize_t1(raw,sc)},ensure_ascii=False))
    print('REFERENCE_BASELINES_PASSED（仅数学内核，不代表完整Agent已实现）')


if __name__=='__main__':
    main()
