"""Hand-calculated tests for the cost model, using the fictional plan_parameters.example.json."""
import copy
import json
import sys
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hsa_cost_model"))
import hsa_cost_model as m

PARAMS = json.loads((ROOT / "plan_parameters" / "plan_parameters.example.json").read_text(encoding="utf-8"))
# example_ppo (current): deductible 500/1000 embedded, OOP 3000/6000 embedded, 20%, copays PCP 25 / specialist 50 /
# urgent 50 / ER 150, Rx no deductible. example_hdhp: deductible 2000/4000 aggregate, OOP 5000/10000 aggregate, 20%.


def claim(id_, member="Member_A", allowed="0", owes="0", paid="0", category="inpatient", coverage="medical",
          network="in_network", month=1, year=2025, source="claims_csv"):
    return m.Claim(id=id_, year=year, month=month, seq=int(id_[-3:]), coverage_type=coverage, category=category,
                   member=member, network=network, allowed=D(allowed), plan_paid=D(paid), member_owes=D(owes),
                   not_covered=D("0"), source=source)


def costs(claims, plan, params=PARAMS):
    return [r.cost for r in m.replay(claims, params, plan)]


def replace_category(c, category):
    import dataclasses
    return dataclasses.replace(c, category=category)


class ReplayTests(unittest.TestCase):
    def test_aggregate_family_deductible_crossed_mid_claim(self):
        claims = [claim("C001", "Member_A", "3000"), claim("C002", "Member_B", "2000", month=2)]
        # A: all 3000 to the 4000 family deductible. B: 1000 deductible + 20% of 1000.
        self.assertEqual(costs(claims, "example_hdhp"), [D("3000.00"), D("1200.00")])

    def test_embedded_family_deductible_stops_at_family_amount(self):
        claims = [claim(f"C00{i}", member, "600", month=i) for i, member in
                  enumerate(("Member_A", "Member_B", "Member_C"), start=1)]
        # 500 + 20% of 100 for A and B; family 1000 met, so C pays only 20% of 600.
        self.assertEqual(costs(claims, "example_ppo"), [D("520.00"), D("520.00"), D("120.00")])

    def test_oop_maximum_caps_cost_and_later_claims_are_free(self):
        claims = [claim("C001", "Member_A", "60000"), claim("C002", "Member_B", "1000", month=2)]
        self.assertEqual(costs(claims, "example_hdhp"), [D("10000.00"), D("0.00")])

    def test_embedded_individual_oop_maximum(self):
        claims = [claim("C001", "Member_A", "60000"), claim("C002", "Member_B", "1000", month=2)]
        # Per-person OOP 3000 caps A; B still pays 500 deductible + 20% of 500.
        self.assertEqual(costs(claims, "example_ppo"), [D("3000.00"), D("600.00")])

    def test_current_plan_can_be_an_hdhp(self):
        params = copy.deepcopy(PARAMS)
        params["current_plan"] = "example_hdhp"
        self.assertEqual(m.current_plan_id(params), "example_hdhp")
        c = claim("C001", allowed="400", owes="400", paid="0", category="medical_unspecified")
        # Under the current HDHP the deductible was used, so the claim is typed as a deductible service.
        self.assertEqual(costs([c], "example_hdhp", params), [D("400.00")])
        self.assertEqual(costs([c], "example_ppo", params), [D("400.00")])
        params["current_plan"] = "nope"
        with self.assertRaises(ValueError):
            m.current_plan_id(params)

    def test_preventive_is_free_and_does_not_accumulate(self):
        claims = [claim("C001", allowed="300", category="preventive", paid="300"), claim("C002", allowed="100", month=2)]
        self.assertEqual(costs(claims, "example_hdhp"), [D("0.00"), D("100.00")])

    def test_accumulators_reset_each_plan_year(self):
        claims = [claim("C001", allowed="2500", year=2025), claim("C002", allowed="2500", year=2026)]
        self.assertEqual(costs(claims, "example_hdhp"), [D("2500.00"), D("2500.00")])

    def test_self_only_tier_uses_individual_limits(self):
        params = copy.deepcopy(PARAMS)
        params["coverage_tier"] = "employee"
        self.assertEqual(costs([claim("C001", allowed="3000")], "example_hdhp", params), [D("2200.00")])

    def test_rx_copay_reused_and_hdhp_deductible_applies(self):
        rx = claim("C001", allowed="15", owes="10", paid="5", category="pharmacy", coverage="pharmacy", network="unknown")
        self.assertEqual(costs([rx], "example_ppo"), [D("10")])
        self.assertEqual(costs([rx], "example_hdhp"), [D("15")])

    def test_rx_tier_maps_to_each_plans_own_copay(self):
        params = copy.deepcopy(PARAMS)
        params["plans"]["example_hdhp"]["in_network"]["pharmacy"]["deductible_applies"] = False
        params["plans"]["example_hdhp"]["in_network"]["pharmacy"]["retail_30"] = [5, 35, 60]
        tier1 = claim("C001", allowed="40", owes="10", paid="30", category="pharmacy", coverage="pharmacy", network="unknown")
        odd = claim("C002", allowed="40", owes="12", paid="28", category="pharmacy", coverage="pharmacy", network="unknown")
        self.assertEqual(m.classify(tier1, params["plans"]["example_ppo"])[0], "rx:retail_30:0")
        self.assertEqual(costs([tier1, odd], "example_hdhp", params), [D("5.00"), D("12")])

    def test_explicit_category_beats_zero_owed_preventive_guess(self):
        c = claim("C001", allowed="5000", owes="0", paid="5000", category="inpatient")
        current = PARAMS["plans"]["example_ppo"]
        self.assertEqual(m.classify(c, current)[0], "inpatient")
        self.assertEqual(m.classify(replace_category(c, "medical_unspecified"), current)[0], "preventive")
        # A paid-in-full extra charge in a copay category is bundled under that visit's copay.
        extra = claim("C002", allowed="4.16", owes="0", paid="4.16", category="urgent_care")
        self.assertEqual(m.classify(extra, current)[0], "bundled:urgent_care")
        self.assertEqual(costs([extra], "example_ppo"), [D("0.00")])
        self.assertEqual(costs([extra], "example_hdhp"), [D("4.16")])

    def test_missing_inputs_are_reported_before_running(self):
        params = copy.deepcopy(PARAMS)
        params["tax"]["state_marginal_rate"] = None
        params["plans"]["example_hdhp"]["out_of_network"]["oop_max"]["family"] = None
        self.assertEqual(m.missing_inputs(params), ["tax.state_marginal_rate",
                                                    "plans.example_hdhp.out_of_network.oop_max.family"])
        self.assertEqual(m.missing_inputs(PARAMS), [])

    def test_several_claims_files_combine_with_unique_ids(self):
        with tempfile.TemporaryDirectory() as t:
            paths = []
            for i in (1, 2):
                p = Path(t) / f"c{i}.csv"
                p.write_text("eob_id,service_month,sequence_in_month,coverage_type,category,member_tag,network,billed,"
                             "discount,not_covered,allowed_estimated,allowed_basis,plan_paid,member_owes,source,review_status\n"
                             "CLM_00001,2025-01,001,medical,inpatient,Member_A,in_network,100,0,0,100,x,80,20,claims_csv,x\n",
                             encoding="utf-8")
                paths.append(p)
            ids = [c.id for c in m.load_claims_files(paths)]
            self.assertEqual(ids, ["CLM_00001", "CLM_00001.2"])

    def test_dental_and_vision_pass_through_unreplayed(self):
        res = m.replay([claim("C001", allowed="200", owes="40", coverage="dental", category="dental")], PARAMS, "example_hdhp")
        self.assertEqual((res[0].replayed, res[0].cost), (False, D("40")))


class ClassificationTests(unittest.TestCase):
    current = PARAMS["plans"]["example_ppo"]

    def svc(self, **kw):
        return m.classify(claim("C001", category="medical_unspecified", **kw), self.current)[0]

    def test_copay_amount_identifies_service(self):
        self.assertEqual(self.svc(allowed="180", owes="25", paid="155"), "office_visit_pcp")
        self.assertEqual(self.svc(allowed="400", owes="150", paid="250"), "emergency")

    def test_copay_capped_at_allowed(self):
        self.assertEqual(self.svc(allowed="34", owes="34", paid="0"), "office_visit_specialist")

    def test_payment_pattern_identifies_deductible_use(self):
        self.assertEqual(self.svc(allowed="100", owes="20", paid="80"), m.NO_DEDUCTIBLE_SERVICE)
        self.assertEqual(self.svc(allowed="400", owes="400", paid="0"), m.DEDUCTIBLE_SERVICE)
        self.assertEqual(self.svc(allowed="300", owes="0", paid="300"), "preventive")

    def test_out_of_network_paid_in_full_is_free_now_but_costs_under_hdhp(self):
        c = claim("C001", allowed="350", owes="0", paid="350", category="medical_unspecified", network="out_of_network")
        self.assertEqual(costs([c], "example_ppo"), [D("0.00")])
        self.assertEqual(costs([c], "example_hdhp"), [D("350.00")])


class EconomicsTests(unittest.TestCase):
    cfg = m.Config()

    def test_net_cost_layers(self):
        n = m.net_cost(PARAMS, "example_hdhp", D("1000.00"), self.cfg)
        rate = D("0.22") + D("0.05") + D("0.0765")
        self.assertEqual(n["premiums"], D("4680.00"))                      # 180 x 26
        self.assertEqual(n["premium_tax_savings"], m.money(D("4680") * rate))
        self.assertEqual((n["employer_hsa"], n["employee_hsa"]), (D("1500.00"), D("7500.00")))
        self.assertEqual(n["hsa_tax_savings"], m.money(D("7500") * rate))
        self.assertEqual(n["net"], n["premiums"] - n["premium_tax_savings"] + D("1000") - D("1500") - n["hsa_tax_savings"])

    def test_current_plan_gets_no_hsa_and_contribution_is_capped(self):
        self.assertEqual(m.hsa_contributions(PARAMS, "example_ppo", self.cfg), (D("0"), D("0")))
        cfg = m.Config(employee_hsa=D("20000"))
        self.assertEqual(m.hsa_contributions(PARAMS, "example_hdhp", cfg)[1], D("7500.00"))

    def test_baseline_reproduces_consistent_history(self):
        claims = [claim("C001", allowed="180", owes="25", paid="155", category="medical_unspecified", year=2025, month=m_)
                  for m_ in range(1, 13)]
        rows = m.baseline_check(claims, PARAMS, self.cfg)
        self.assertEqual([(r["year"], r["passes"]) for r in rows], [(2025, True)])
        self.assertAlmostEqual(rows[0]["accuracy"], 1.0)

    def test_break_even_finds_crossing_between_plans(self):
        claims = [claim(f"C{i:03d}", allowed="100", owes="25", paid="75", category="medical_unspecified", month=i)
                  for i in range(1, 13)]
        _grid, crossings = m.break_even(claims, PARAMS, m.Config(breakeven_step=D("1000")))
        self.assertEqual(len(crossings), 1)
        self.assertEqual(crossings[0]["cheaper_at_base"], "example_hdhp")

    def test_workbook_has_live_formulas(self):
        claims = [claim(f"C{i:03d}", allowed="180", owes="25", paid="155", category="medical_unspecified", month=i)
                  for i in range(1, 13)]
        from openpyxl import load_workbook
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / "cmp.xlsx"
            m.build_workbook(claims, PARAMS, m.Config(), out, {"claims": "claims.csv", "plans": "plans.json"})
            wb = load_workbook(out)
            self.assertEqual(wb.sheetnames[0], "Annual comparison")
            for name in ("Scenarios", "Break-even", "Cash flow", "Projection", "Sensitivity", "Baseline check",
                         "Historical", "Claims replay", "Parameters", "Assumptions"):
                self.assertIn(name, wb.sheetnames)
            ac = wb["Annual comparison"]
            self.assertTrue(str(ac["I5"].value).startswith("="))
            self.assertTrue(str(wb["Projection"]["H12"].value or wb["Projection"]["H11"].value).startswith("="))
            self.assertTrue(all(str(ws["A1"].value).startswith("ESTIMATE") for ws in wb.worksheets))

    def test_estimates_note_reflects_the_claims(self):
        from openpyxl import load_workbook
        actual = [claim(f"C{i:03d}", allowed="180", owes="25", paid="155", category="medical_unspecified", month=i)
                  for i in range(1, 13)]
        estimate = claim("E001", allowed="40", owes="10", paid="30", category="vision", coverage="vision",
                         source="estimate")

        def notes(claims):
            with tempfile.TemporaryDirectory() as t:
                out = Path(t) / "cmp.xlsx"
                m.build_workbook(claims, PARAMS, m.Config(), out, {"claims": "claims.csv", "plans": "plans.json"})
                return " ".join(str(r[0].value) for r in load_workbook(out)["Assumptions"].iter_rows())

        self.assertNotIn("user-entered estimates", notes(actual))
        self.assertIn("Some vision costs are user-entered estimates", notes(actual + [estimate]))


if __name__ == "__main__":
    unittest.main()
