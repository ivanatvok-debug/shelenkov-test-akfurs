"""Synthetic cases; these records are unrelated to the employer's corpus."""

import copy
import json
import unittest

from tools.data_processing.reconciliation import reconcile


def record(identifier, amount="100.00", reference="R001", **extra):
    return {"id": identifier, "amount": amount, "reference": reference,
            "currency": "RUB", "date": "2026-01-10", **extra}


def rules(**extra):
    return {"keys": [["reference"]], "currencies": ["RUB"],
            "amount_tolerance": "0", "date_tolerance_days": 2,
            "allowed_types": ["exact"], "rule_order": ["exact"], **extra}


class ReconciliationTests(unittest.TestCase):
    def test_unrecognized_payment_retains_raw_and_normalized_values(self):
        tx = record("T1", "1.20", reference="other", memo="Synthetic note")
        result = reconcile([record("O1")], [tx], rules())
        unresolved = result["unresolved_transactions"][0]
        self.assertEqual(unresolved["raw"], tx)
        self.assertEqual(unresolved["normalized"], {"amount": "1.20", "currency": "RUB", "date": "2026-01-10"})
        unresolved["raw"]["memo"] = "changed"
        self.assertEqual(tx["memo"], "Synthetic note")

    def test_invalid_related_source_cannot_remove_identity_ambiguity(self):
        duplicate_order = reconcile([record("O1"), record("O2", "bad")], [record("T1")], rules())
        self.assertEqual(duplicate_order["results"][0]["status"], "AMBIGUOUS")
        invalid_payment = reconcile([record("O1")], [record("T1"), record("T2", "bad")], rules())
        self.assertEqual(invalid_payment["results"][0]["status"], "CONFLICT")
        self.assertEqual(invalid_payment["results"][0]["links"], [])
        self.assertEqual(len(invalid_payment["unresolved_transactions"]), 2)

    def test_T07_exact_payment_preserves_identifiers_and_trace(self):
        orders = [record("0001")]
        transactions = [record("0007")]
        result = reconcile(orders, transactions, rules())
        row = result["results"][0]
        self.assertEqual(row["status"], "MATCHED")
        self.assertEqual(row["order_id"], "0001")
        self.assertEqual(row["links"], [{"transaction_id": "0007", "amount": "100.00"}])
        self.assertEqual(row["raw"]["order"], orders[0])
        self.assertEqual(row["normalized"]["payment_total"], "100.00")
        self.assertEqual(result["unresolved_transactions"], [])

    def test_T08_same_amount_date_is_not_identity(self):
        result = reconcile([record("O1", reference="A"), record("O2", reference="B")],
                           [record("T1", reference="")], rules())
        self.assertEqual([r["status"] for r in result["results"]], ["UNMATCHED", "UNMATCHED"])
        self.assertEqual(len(result["unresolved_transactions"]), 1)

    def test_duplicate_confirmed_key_is_ambiguous(self):
        result = reconcile([record("O1"), record("O2")], [record("T1")], rules())
        self.assertEqual([r["status"] for r in result["results"]], ["AMBIGUOUS", "AMBIGUOUS"])
        self.assertTrue(all(not r["links"] for r in result["results"]))

    def test_T09_partial_payment_remains_review_required(self):
        result = reconcile([record("O1")], [record("T1", "40.00")],
                           rules(allowed_types=["exact", "partial"]))
        row = result["results"][0]
        self.assertEqual(row["status"], "REVIEW_REQUIRED")
        self.assertEqual(row["normalized"]["payment_total"], "40.00")
        self.assertIn("PARTIAL_PAYMENT", row["reasons"])
        self.assertEqual(result["summary"]["matched_totals"], {})

    def test_T10_transaction_reuse_conflicts_for_both_orders(self):
        orders = [record("O1", reference="A", alt="X"), record("O2", reference="B", alt="Y")]
        transactions = [record("T1", reference="A", alt="Y")]
        result = reconcile(orders, transactions, rules(keys=[["reference"], ["alt"]]))
        self.assertEqual([r["status"] for r in result["results"]], ["CONFLICT", "CONFLICT"])
        self.assertTrue(all("TRANSACTION_REUSE" in r["reasons"] for r in result["results"]))
        self.assertEqual(len(result["unresolved_transactions"]), 1)

    def test_T11_decimal_partial_sum_is_exact(self):
        result = reconcile([record("O1", "0.30")],
                           [record("T1", "0.10"), record("T2", "0.20")],
                           rules(allowed_types=["exact", "partial"]))
        self.assertEqual(result["results"][0]["status"], "MATCHED")
        self.assertEqual(result["results"][0]["normalized"]["payment_total"], "0.30")
        self.assertEqual(result["summary"]["matched_totals"], {"RUB": "0.30"})

    def test_T13_repeat_and_input_order_do_not_change_decisions(self):
        orders = [record("O1", reference="A"), record("O2", reference="B")]
        transactions = [record("T2", reference="B"), record("T1", reference="A")]
        first = reconcile(orders, transactions, rules())
        second = reconcile(orders, transactions, rules())
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        reversed_result = reconcile(list(reversed(orders)), list(reversed(transactions)), rules())
        self.assertEqual({r["order_id"]: r["status"] for r in first["results"]},
                         {r["order_id"]: r["status"] for r in reversed_result["results"]})

    def test_T15_fuzzy_candidates_never_confirm_financial_match(self):
        result = reconcile([record("O1", reference="A", name="Synthetic Smith")],
                           [record("T1", reference="B", name="Synthetic Smit")],
                           rules(allowed_types=["exact", "fuzzy"],
                                 rule_order=["exact", "fuzzy"],
                                 candidate_field="name", fuzzy_threshold="0.8"))
        row = result["results"][0]
        self.assertEqual(row["status"], "REVIEW_REQUIRED")
        self.assertEqual(row["links"], [])
        self.assertEqual(row["candidates"], ["T1"])
        self.assertTrue(row["uncertainty"])

    def test_normalized_string_candidates_do_not_become_exact_matches(self):
        result = reconcile([record("O1", reference="A", name="  АЛЬФА  ")],
                           [record("T1", reference="B", name="альфа")],
                           rules(allowed_types=["normalized"], rule_order=["normalized"],
                                 candidate_field="name"))
        self.assertEqual(result["results"][0]["status"], "REVIEW_REQUIRED")
        self.assertEqual(result["results"][0]["rule"], "normalized:name")

    def test_composite_key_requires_all_components(self):
        options = rules(keys=[["customer", "product"]], allowed_types=["composite"],
                        rule_order=["composite"])
        result = reconcile([record("O1", customer="001", product="P1")],
                           [record("T1", customer="001", product="P2"),
                            record("T2", customer="001", product="P1")], options)
        self.assertEqual(result["results"][0]["links"], [{"transaction_id": "T2", "amount": "100.00"}])

    def test_currency_mismatch_is_never_matched(self):
        result = reconcile([record("O1")], [record("T1", currency="USD")],
                           rules(currencies=["RUB", "USD"]))
        self.assertEqual(result["results"][0]["status"], "CONFLICT")
        self.assertIn("CURRENCY_MISMATCH", result["results"][0]["reasons"])

    def test_amount_and_date_tolerances_are_explicit(self):
        order, tx = record("O1"), record("T1", "100.01", date="2026-01-12")
        self.assertEqual(reconcile([order], [tx], rules())["results"][0]["status"], "CONFLICT")
        accepted = reconcile([order], [tx], rules(amount_tolerance="0.01"))
        self.assertEqual(accepted["results"][0]["status"], "MATCHED")
        tx["date"] = "2026-01-13"
        self.assertEqual(reconcile([order], [tx], rules(amount_tolerance="0.01"))["results"][0]["status"], "CONFLICT")
        with self.assertRaises(ValueError):
            bad_rules = rules(); del bad_rules["amount_tolerance"]
            reconcile([order], [tx], bad_rules)

    def test_float_money_and_invalid_dates_are_retained_as_exceptions(self):
        result = reconcile([record("O1", 0.1), record("O2", date="10/01/2026")],
                           [record("T1")], rules())
        self.assertEqual([r["status"] for r in result["results"]], ["CONFLICT", "CONFLICT"])
        self.assertEqual(len(result["exceptions"]), 2)
        self.assertEqual({e["source_index"] for e in result["exceptions"]}, {0, 1})

    def test_duplicate_source_ids_are_not_collapsed(self):
        result = reconcile([record("O1")], [record("T1"), record("T1")], rules())
        self.assertNotEqual(result["results"][0]["status"], "MATCHED")
        self.assertEqual(len(result["unresolved_transactions"]), 2)
        self.assertEqual(len(result["exceptions"]), 2)

    def test_group_payment_requires_complete_explicit_allocation(self):
        orders = [record("O1", "60.00", reference="A"), record("O2", "40.00", reference="B")]
        transactions = [record("T1")]
        options = rules(allowed_types=["exact", "group"], allocations=[
            {"transaction_id": "T1", "order_id": "O1", "amount": "60.00"},
            {"transaction_id": "T1", "order_id": "O2", "amount": "40.00"}])
        result = reconcile(orders, transactions, options)
        self.assertEqual([r["status"] for r in result["results"]], ["MATCHED", "MATCHED"])
        self.assertEqual(result["summary"]["matched_totals"], {"RUB": "100.00"})
        options["allocations"][1]["amount"] = "39.00"
        with self.assertRaises(ValueError):
            reconcile(orders, transactions, options)

    def test_refund_and_signed_payment_aggregation_require_permission(self):
        options = rules(allowed_types=["exact", "partial", "refund"])
        result = reconcile([record("O1", "80.00")],
                           [record("T1", "100.00"), record("T2", "-20.00")], options)
        self.assertEqual(result["results"][0]["status"], "MATCHED")
        refused = reconcile([record("O1", "-20.00")], [record("T1", "-20.00")], rules())
        self.assertEqual(refused["results"][0]["status"], "REVIEW_REQUIRED")
        accepted = reconcile([record("O1", "-20.00")], [record("T1", "-20.00")], options)
        self.assertEqual(accepted["results"][0]["status"], "MATCHED")

    def test_financial_precision_is_not_limited_by_decimal_default_context(self):
        amount = "123456789012345678901234567890.123456789"
        result = reconcile([record("O1", amount)], [record("T1", amount)], rules())
        self.assertEqual(result["summary"]["matched_totals"], {"RUB": amount})

    def test_input_records_and_rules_are_not_mutated(self):
        orders, transactions, options = [record("O1")], [record("T1")], rules()
        before = copy.deepcopy((orders, transactions, options))
        result = reconcile(orders, transactions, options)
        self.assertEqual((orders, transactions, options), before)
        result["results"][0]["raw"]["order"]["amount"] = "changed"
        self.assertEqual(orders[0]["amount"], "100.00")


if __name__ == "__main__":
    unittest.main()
