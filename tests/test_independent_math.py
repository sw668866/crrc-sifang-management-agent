"""使用Python Fraction和第二套穷举逻辑独立交叉复算冻结教材模型。"""

import random
import sys
import unittest
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_core import calculate_t0, calculate_t1, load_frozen_data  # noqa: E402


def f(number):
    return Fraction(str(number))


class TestIndependentAccounting(unittest.TestCase):
    def test_t0_two_stage_allocation_with_independent_fraction_arithmetic(self):
        data = load_frozen_data()["T0"]
        d = data["resource_usage"]
        staff = f(data["fixed_resource_costs"]["staff_support"])
        equipment = f(data["fixed_resource_costs"]["equipment_depreciation_support"])
        staff_total = sum(map(f, d["staff_hours"].values()))
        equipment_total = sum(map(f, d["support_equipment_hours"].values()))
        rate_staff = staff / staff_total
        rate_equipment = equipment / equipment_total
        pools = {
            a: f(d["staff_hours"][a]) * rate_staff
            + f(d["support_equipment_hours"][a]) * rate_equipment
            for a in ("preparation", "inspection_support")
        }
        costs = {}
        for p in ("X", "Y"):
            costs[p] = sum(
                pools[a] / sum(f(x) for x in data["activity_occurrences"][a].values())
                * f(data["activity_occurrences"][a][p])
                for a in pools
            )
        result = calculate_t0()["result"]
        self.assertEqual(sum(costs.values()), staff + equipment)
        self.assertEqual(result["allocated_support_cost"], {p: int(costs[p]) for p in costs})
        self.assertEqual(result["pools"], {p: int(pools[p]) for p in pools})
        for p in costs:
            q = f(data["quantity"][p])
            margin = f(data["price_per_unit"][p]) - f(data["avoidable_variable_outlay_per_unit"][p])
            self.assertEqual(f(result["unit_internal_residual"][p]), margin - costs[p] / q)

    def test_three_standard_optima_with_different_integer_solver(self):
        data = load_frozen_data()["T1"]
        cases = [(300, 1900, (75, 100, 131250)),
                 (350, 1900, (100, 100, 150000)),
                 (300, 1700, (99, 68, 111650))]
        for capacity, y_price, expected in cases:
            with self.subTest(capacity=capacity, y_price=y_price):
                all_candidates = [
                    (x * (1700 - 950) + y * (y_price - 1150), x, y)
                    for x in range(data["maximum_quantity"]["X"] + 1)
                    for y in range(data["maximum_quantity"]["Y"] + 1)
                    if 4*x + 3*y <= 2*capacity
                ]
                contribution, x, y = max(all_candidates)
                self.assertEqual((x, y, contribution), expected)
                actual = calculate_t1({"total_capacity_hours": capacity,
                                       "price_per_unit": {"Y": y_price}})["result"]
                self.assertEqual((actual["x"], actual["y"], actual["total_contribution"]), expected)

    def test_random_integer_scenarios_independent_fraction_exhaustive(self):
        rng = random.Random(20261028)
        for _ in range(120):
            h = rng.randrange(0, 41)
            x_hour = rng.choice((1, 1.25, 1.5, 2, 2.5))
            y_hour = rng.choice((1, 1.5, 1.75, 2, 2.5))
            px, py = rng.randrange(0, 2501, 50), rng.randrange(0, 2501, 50)
            vx, vy = rng.randrange(0, 2101, 50), rng.randrange(0, 2101, 50)
            max_x, max_y = rng.randrange(0, 14), rng.randrange(0, 14)
            min_x, min_y = rng.randrange(0, max_x + 1), rng.randrange(0, max_y + 1)
            extra = rng.randrange(0, 5001, 100)
            kwargs = {
                "total_capacity_hours": h,
                "price_per_unit": {"X": px, "Y": py},
                "avoidable_variable_outlay_per_unit": {"X": vx, "Y": vy},
                "bottleneck_processing_hours_per_unit": {"X": x_hour, "Y": y_hour},
                "maximum_quantity": {"X": max_x, "Y": max_y},
                "minimum_quantity": {"X": min_x, "Y": min_y},
                "additional_fixed_outlay_for_scenario": extra,
            }
            feasible = [
                ((f(px) - f(vx))*x + (f(py) - f(vy))*y, x, y)
                for x in range(min_x, max_x + 1)
                for y in range(min_y, max_y + 1)
                if f(x_hour)*x + f(y_hour)*y <= f(h)
            ]
            actual = calculate_t1(kwargs)["result"]
            if not feasible:
                self.assertEqual(actual["status"], "infeasible", kwargs)
                continue
            optimum = max(row[0] for row in feasible)
            winning = {(x, y) for value, x, y in feasible if value == optimum}
            self.assertEqual(actual["status"], "optimal", kwargs)
            self.assertEqual(f(actual["total_contribution"]), optimum, kwargs)
            self.assertEqual(f(actual["simplified_period_residual"]), optimum - 100000 - extra, kwargs)
            self.assertIn((actual["x"], actual["y"]), winning, kwargs)
            self.assertEqual(actual["optimal_solution_count"], len(winning), kwargs)


if __name__ == "__main__":
    unittest.main()
