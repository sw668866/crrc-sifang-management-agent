"""Python标准库 unittest：基准、独立计算、非法输入、版本隔离及费用守恒测试。"""
import copy
import importlib.util
import json
import math
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
CORE = HERE / '中车四方_确定性核算内核_v2_2.py'
JSON = HERE / '中车四方_教学参数_v2_2.json'
spec = importlib.util.spec_from_file_location('deterministic_core_v22', CORE)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

class TestTeachingModel(unittest.TestCase):
    def setUp(self):
        self.d = json.loads(JSON.read_text(encoding='utf-8'))
        self.base = copy.deepcopy(self.d['T1']['scenarios'][0])

    def test_t0_baseline(self):
        r=core.verify_t0(self.d)
        self.assertEqual(r['pools'], {'preparation':60000,'inspection_support':40000})
        self.assertEqual(r['allocated_support_cost'], {'X':22000,'Y':78000})
        self.assertEqual(r['unit_internal_residual'], {'X':530,'Y':-30})
        self.assertEqual(r['simplified_period_residual'],50000)
    def test_t0_all_expected(self):
        core.validate_baseline_expectations(self.d)
    def test_t0_mutated_expected_fails(self):
        self.d['T0']['expected']['allocated_support_cost']['X']=99999
        with self.assertRaises(AssertionError):core.validate_baseline_expectations(self.d)
    def test_t0_zero_staff_hours(self):
        self.d['T0']['resource_usage']['staff_hours']={'preparation':0,'inspection_support':0}
        with self.assertRaises(core.ModelInputError):core.verify_t0(self.d)
    def test_t0_negative_usage(self):
        self.d['T0']['resource_usage']['staff_hours']['preparation']=-10
        with self.assertRaises(core.ModelInputError):core.verify_t0(self.d)
    def test_t0_zero_activity_driver(self):
        self.d['T0']['activity_occurrences']['inspection_support']={'X':0,'Y':0}
        with self.assertRaises(core.ModelInputError):core.verify_t0(self.d)
    def test_t0_changed_cost_data_compute_dynamically(self):
        self.d['T0']['fixed_resource_costs']['staff_support']=66000
        r=core.verify_t0(self.d)
        self.assertNotEqual(r['allocated_support_cost']['X'],22000)
        self.assertAlmostEqual(sum(r['allocated_support_cost'].values()),106000)
    def test_t0_fractional_resource_rates(self):
        self.d['T0']['resource_usage']['staff_hours']['inspection_support']=201
        r=core.verify_t0(self.d)
        self.assertAlmostEqual(sum(r['allocated_support_cost'].values()),100000)
    def test_three_baselines(self):
        for s in self.d['T1']['scenarios']:
            with self.subTest(case=s['id']):
                r=core.optimize_t1(self.d,s)
                self.assertEqual((r['x'],r['y']),(s['expected_optimal_quantity']['X'],s['expected_optimal_quantity']['Y']))
                self.assertEqual(r['total_contribution'],s['expected_total_contribution'])
    def test_independent_one_dimensional_analytic_optimizer(self):
        # 独立核算路径：仅枚举x，选择允许的最大y（本组两类贡献均为正）
        for s in self.d['T1']['scenarios']:
            h=s['total_capacity_hours']; py=s['price_per_unit']['Y']; cm_y=py-1150
            best = -float('inf'); winners=[]
            for x in range(101):
                ymax=min(100,int((h-2*x)//1.5))
                if ymax<0: continue
                y=ymax if cm_y>=0 else 0
                z=750*x+cm_y*y
                if z>best: best=z;winners=[(x,y)]
                elif z==best:winners.append((x,y))
            r=core.optimize_t1(self.d,s)
            self.assertEqual((r['x'],r['y'],r['total_contribution']),(*winners[0],best))
    def test_negative_capacity(self):
        self.base['total_capacity_hours']=-1
        with self.assertRaises(core.ModelInputError):core.optimize_t1(self.d,self.base)
    def test_nan_capacity(self):
        self.base['total_capacity_hours']=float('nan')
        with self.assertRaises(core.ModelInputError):core.optimize_t1(self.d,self.base)
    def test_bool_capacity(self):
        self.base['total_capacity_hours']=True
        with self.assertRaises(core.ModelInputError):core.optimize_t1(self.d,self.base)
    def test_nan_price(self):
        self.base['price_per_unit']['X']=float('nan')
        with self.assertRaises(core.ModelInputError):core.optimize_t1(self.d,self.base)
    def test_non_numeric_price(self):
        self.base['price_per_unit']['X']='1700'
        with self.assertRaises(core.ModelInputError):core.optimize_t1(self.d,self.base)
    def test_missing_y_price(self):
        del self.base['price_per_unit']['Y']
        with self.assertRaises(core.ModelInputError):core.optimize_t1(self.d,self.base)
    def test_nan_unit_hours(self):
        self.d['T1']['bottleneck_processing_hours_per_unit']['Y']=float('nan')
        with self.assertRaises(core.ModelInputError):core.optimize_t1(self.d,self.base)
    def test_bool_demand(self):
        self.d['T1']['minimum_quantity']['X']=True
        with self.assertRaises(core.ModelInputError):core.optimize_t1(self.d,self.base)
    def test_fractional_demand(self):
        self.base['maximum_quantity']={'X':99.5,'Y':100}
        with self.assertRaises(core.ModelInputError):core.optimize_t1(self.d,self.base)
    def test_unknown_contract_cost_cannot_be_ignored(self):
        self.base['contract_penalty']=99999
        with self.assertRaises(core.ModelInputError):core.optimize_t1(self.d,self.base)
    def test_unknown_t0_cost_field_rejected(self):
        self.d['T0']['hypothetical_contract_cost']=100000
        with self.assertRaises(core.ModelInputError):core.verify_t0(self.d)
    def test_unknown_t1_business_field_rejected(self):
        self.d['T1']['hypothetical_contract_cost']=100000
        with self.assertRaises(core.ModelInputError):core.optimize_t1(self.d,self.base)
    def test_price_elastic_demand_without_new_bound_rejected(self):
        self.d['T1']['price_sensitivity_keeps_demand_fixed']=False
        self.base['price_per_unit']['Y']=1700
        with self.assertRaises(core.ModelInputError):core.optimize_t1(self.d,self.base)
    def test_zero_capacity(self):
        self.base['total_capacity_hours']=0
        r=core.optimize_t1(self.d,self.base)
        self.assertEqual((r['x'],r['y'],r['total_contribution']),(0,0,0))
    def test_mandatory_infeasible(self):
        self.base['total_capacity_hours']=0
        self.base['minimum_quantity']={'X':1,'Y':0}
        self.assertEqual(core.optimize_t1(self.d,self.base)['status'],'infeasible')
    def test_zero_demand(self):
        self.base['maximum_quantity']={'X':0,'Y':0}
        r=core.optimize_t1(self.d,self.base)
        self.assertEqual((r['x'],r['y']),(0,0))
    def test_negative_contribution_not_selected(self):
        self.base['price_per_unit']['Y']=1000
        r=core.optimize_t1(self.d,self.base)
        self.assertEqual(r['y'],0)
    def test_multi_optima(self):
        self.base['price_per_unit']['Y']=1150
        r=core.optimize_t1(self.d,self.base)
        self.assertGreater(r['optimal_solution_count'],1)
        self.assertEqual(r['x'],100)
    def test_sensitivity_variable_cost(self):
        self.base['avoidable_variable_outlay_per_unit']={'X':1100,'Y':1150}
        r=core.optimize_t1(self.d,self.base)
        self.assertEqual(r['unit_contribution']['X'],600)
        self.assertEqual(core.verify_t0(self.d)['unit_internal_residual']['X'],530)
    def test_sensitivity_demand(self):
        self.base['maximum_quantity']={'X':40,'Y':100}
        r=core.optimize_t1(self.d,self.base)
        self.assertLessEqual(r['x'],40)
    def test_scenario_extra_cost_explained(self):
        self.base['additional_fixed_outlay_for_scenario']=5000
        r=core.optimize_t1(self.d,self.base)
        self.assertEqual(r['total_contribution'],131250)
        self.assertEqual(r['simplified_period_residual'],26250)
    def test_t1_flags_fail_closed(self):
        self.d['T1']['production_equals_sales']=False
        with self.assertRaises(core.ModelInputError):core.optimize_t1(self.d,self.base)
    def test_extreme_candidate_count_rejected(self):
        self.base['maximum_quantity']={'X':2000,'Y':2000}
        with self.assertRaises(core.ModelInputError):core.optimize_t1(self.d,self.base)
    def test_tamper_t1_expected_detected(self):
        self.d['T1']['scenarios'][0]['expected_total_contribution']=0
        with self.assertRaises(AssertionError):core.validate_baseline_expectations(self.d)

    def test_contract_rejects_wrong_enterprise_identity(self):
        self.d['meta']['source_type']='real_enterprise_ledger'
        with self.assertRaises(core.ModelInputError): core.validate_baseline_expectations(self.d)

    def test_contract_rejects_base_capacity_mismatch(self):
        self.d['T1']['base_total_bottleneck_hours']=299
        with self.assertRaises(core.ModelInputError): core.validate_baseline_expectations(self.d)

    def test_contract_rejects_base_price_mismatch(self):
        self.d['T1']['price_per_unit_base']['X']=1800
        with self.assertRaises(core.ModelInputError): core.validate_baseline_expectations(self.d)

    def test_contract_rejects_false_restoration_flag(self):
        self.d['T1']['base_restored_350_scenario_extra_cost_is_zero']=False
        with self.assertRaises(core.ModelInputError): core.validate_baseline_expectations(self.d)

    def test_contract_rejects_new_hidden_override(self):
        self.d['T1']['dynamic_overrides_supported'].append('contract_penalty')
        with self.assertRaises(core.ModelInputError): core.validate_baseline_expectations(self.d)

    def test_contract_rejects_edited_product_identity(self):
        self.d['entity']['fictional_product_labels']=['X','Y','Z']
        with self.assertRaises(core.ModelInputError): core.validate_baseline_expectations(self.d)

    def test_contract_rejects_fake_expected_cost(self):
        self.d['T0']['expected']['contract_penalty']=999999
        with self.assertRaises(core.ModelInputError): core.validate_baseline_expectations(self.d)

    def test_qty_guard_rejects_extreme_mandatory_numbers(self):
        self.base['minimum_quantity']={'X':10**30,'Y':0}
        self.base['maximum_quantity']={'X':10**30,'Y':0}
        with self.assertRaises(core.ModelInputError): core.optimize_t1(self.d,self.base)

    def test_contract_rejects_duplicate_scenario_ids(self):
        self.d['T1']['scenarios'][1]['id']='base_300'
        with self.assertRaises(core.ModelInputError): core.validate_baseline_expectations(self.d)

    def test_legitimate_dynamic_cost_change_does_not_rewrite_T0(self):
        original=core.verify_t0(self.d)
        self.base['avoidable_variable_outlay_per_unit']={'X':990,'Y':1130}
        result=core.optimize_t1(self.d,self.base)
        self.assertEqual(result['unit_contribution'],{'X':710,'Y':770})
        self.assertEqual(core.verify_t0(self.d),original)


    def test_contract_rejects_restored_400_even_same_optimum(self):
        self.d['T1']['scenarios'][1]['total_capacity_hours']=400
        with self.assertRaises(core.ModelInputError):core.validate_baseline_expectations(self.d)

    def test_contract_rejects_discount_300_2_same_optimum(self):
        self.d['T1']['scenarios'][2]['total_capacity_hours']=300.2
        with self.assertRaises(core.ModelInputError):core.validate_baseline_expectations(self.d)

    def test_contract_rejects_missing_discount_scenario(self):
        self.d['T1']['scenarios'].pop()
        with self.assertRaises(core.ModelInputError):core.validate_baseline_expectations(self.d)

    def test_contract_rejects_real_ledger_T0_note(self):
        self.d['T0']['data_character']='中车四方实际账目'
        with self.assertRaises(core.ModelInputError):core.validate_baseline_expectations(self.d)

    def test_contract_rejects_real_ledger_T1_note(self):
        self.d['T1']['data_character']='中车四方真实排产数据'
        with self.assertRaises(core.ModelInputError):core.validate_baseline_expectations(self.d)

    def test_contract_rejects_fake_status(self):
        self.d['meta']['status']='企业真实财务台账'
        with self.assertRaises(core.ModelInputError):core.validate_baseline_expectations(self.d)

    def test_contract_rejects_fake_usage(self):
        self.d['meta']['usage']='中车内部生产排程正式数据'
        with self.assertRaises(core.ModelInputError):core.validate_baseline_expectations(self.d)

    def test_contract_rejects_entity_name_change(self):
        self.d['entity']['name']='其它企业'
        with self.assertRaises(core.ModelInputError):core.validate_baseline_expectations(self.d)

    def test_contract_rejects_entity_evidence_change(self):
        self.d['entity']['evidence']='企业授权真实台账'
        with self.assertRaises(core.ModelInputError):core.validate_baseline_expectations(self.d)

    def test_contract_rejects_empty_cross_period_rules(self):
        self.d['cross_period_rules']=[]
        with self.assertRaises(core.ModelInputError):core.validate_baseline_expectations(self.d)

    def test_contract_rejects_empty_validation_tests(self):
        self.d['validation_tests']=[]
        with self.assertRaises(core.ModelInputError):core.validate_baseline_expectations(self.d)

    def test_contract_rejects_validation_test_renaming(self):
        self.d['validation_tests'][0]['test']='真实订单已验证'
        with self.assertRaises(core.ModelInputError):core.validate_baseline_expectations(self.d)

    def test_contract_all_baselines_intact(self):
        core.validate_baseline_expectations(self.d)

    def test_contract_rejects_base_minimum_change_without_result_change(self):
        self.d['T1']['minimum_quantity']['X']=1
        with self.assertRaises(core.ModelInputError):core.validate_baseline_expectations(self.d)

    def test_contract_rejects_base_maximum_change_without_result_change(self):
        self.d['T1']['maximum_quantity']['X']=101
        with self.assertRaises(core.ModelInputError):core.validate_baseline_expectations(self.d)

    def test_contract_rejects_scenario_name_change_without_result_change(self):
        self.d['T1']['scenarios'][2]['name']='企业真实订单'
        with self.assertRaises(core.ModelInputError):core.validate_baseline_expectations(self.d)

    def test_randomized_exact_fraction_optimization(self):
        import random
        from fractions import Fraction
        rng=random.Random(20261027)
        for i in range(100):
            maxx=rng.randrange(0,22);maxy=rng.randrange(0,22)
            mnx=rng.randrange(0,maxx+1);mny=rng.randrange(0,maxy+1)
            px=rng.randrange(0,3000);py=rng.randrange(0,3000)
            vx=rng.randrange(0,1500);vy=rng.randrange(0,1500)
            hx=Fraction(rng.randrange(1,20),4);hy=Fraction(rng.randrange(1,20),4)
            h=Fraction(rng.randrange(0,100),4)
            scenario={'total_capacity_hours':float(h),'price_per_unit':{'X':px,'Y':py},
              'avoidable_variable_outlay_per_unit':{'X':vx,'Y':vy},
              'bottleneck_processing_hours_per_unit':{'X':float(hx),'Y':float(hy)},
              'minimum_quantity':{'X':mnx,'Y':mny},'maximum_quantity':{'X':maxx,'Y':maxy}}
            expected_best=None; winners=[]
            for x in range(mnx,maxx+1):
                for y in range(mny,maxy+1):
                    if hx*x+hy*y>h:continue
                    z=(px-vx)*x+(py-vy)*y
                    if expected_best is None or z>expected_best:
                        expected_best=z;winners=[(x,y)]
                    elif z==expected_best:winners.append((x,y))
            got=core.optimize_t1(self.d,scenario)
            if expected_best is None:
                self.assertEqual(got['status'],'infeasible')
            else:
                self.assertEqual(got['total_contribution'],expected_best)
                self.assertEqual(got['optimal_solution_count'],len(winners))
                self.assertIn((got['x'],got['y']),winners)


if __name__=='__main__':
    unittest.main(verbosity=2)
