"""Tests for the plan-parameters validator, using the committed fictional example."""
import copy
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import validate_plan_parameters as vpp

EXAMPLE = json.loads((HERE / "plan_parameters.example.json").read_text(encoding="utf-8"))


class ValidatePlanParametersTests(unittest.TestCase):
    def test_example_is_valid(self):
        r = vpp.validate(EXAMPLE)
        self.assertEqual(r.errors, [])
        self.assertEqual(r.missing, ["tax.healthcare_fsa_annual"])

    def test_embedded_individual_deductible_below_irs_family_minimum_is_rejected(self):
        data = copy.deepcopy(EXAMPLE)
        data["plans"]["example_hdhp"]["in_network"]["deductible"]["family_type"] = "embedded"
        r = vpp.validate(data)
        self.assertTrue(any("below the IRS family minimum" in e for e in r.errors))
        data["coverage_tier"] = "employee"
        self.assertEqual(vpp.validate(data).errors, [])

    def test_hdhp_outside_irs_bounds_is_rejected(self):
        data = copy.deepcopy(EXAMPLE)
        data["plans"]["example_hdhp"]["in_network"]["oop_max"]["family"] = 20000
        data["plans"]["example_hdhp"]["in_network"]["deductible"]["individual"] = 1000
        errors = " ".join(vpp.validate(data).errors)
        self.assertIn("oop_fam", errors)
        self.assertIn("ded_ind", errors)

    def test_null_values_are_reported_as_missing_not_errors(self):
        data = copy.deepcopy(EXAMPLE)
        data["plans"]["example_hdhp"]["in_network"]["oop_max"]["family"] = None
        del data["plans"]["example_hdhp"]["in_network"]["services"]["urgent_care"]
        r = vpp.validate(data)
        self.assertEqual(r.errors, [])
        self.assertIn("plans.example_hdhp.in_network.oop_max.family", r.missing)
        self.assertIn("plans.example_hdhp.in_network.services.urgent_care", r.missing)

    def test_copay_rule_requires_amount_and_unknown_rule_is_rejected(self):
        data = copy.deepcopy(EXAMPLE)
        services = data["plans"]["example_ppo"]["in_network"]["services"]
        del services["urgent_care"]["copay"]
        services["emergency"] = {"rule": "free"}
        errors = " ".join(vpp.validate(data).errors)
        self.assertIn("urgent_care.copay: required", errors)
        self.assertIn("emergency.rule", errors)

    def test_fsa_warns_for_hsa_plans(self):
        data = copy.deepcopy(EXAMPLE)
        data["tax"]["healthcare_fsa_annual"] = 1000
        self.assertTrue(any("FSA" in w for w in vpp.validate(data).warnings))


if __name__ == "__main__":
    unittest.main()
