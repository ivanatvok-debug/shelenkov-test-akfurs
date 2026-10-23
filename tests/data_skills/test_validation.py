import copy
import importlib.util
import json
from pathlib import Path
import unittest

from tools.data_processing.validation import validate_result


def specimen():
    order = {"id": "O1", "amount": "0.30", "currency": "RUB", "date": "2026-01-10", "reference": "R1"}
    tx = {"id": "T1", "amount": "0.30", "currency": "RUB", "date": "2026-01-10", "reference": "R1"}
    rules = {"keys": [["reference"]], "currencies": ["RUB"], "amount_tolerance": "0",
             "date_tolerance_days": 0, "allowed_types": ["exact"], "rule_order": ["exact"]}
    result = {"version": "1.0", "results": [{
        "source_index": 0, "order_id": "O1", "status": "MATCHED", "rule": "exact:reference",
        "links": [{"transaction_id": "T1", "amount": "0.30"}], "candidates": ["T1"],
        "transaction_source_indices": [0], "raw": {"order": order, "transactions": [tx]},
        "normalized": {"order_amount": "0.30", "payment_total": "0.30", "currency": "RUB", "date": "2026-01-10"},
        "reasons": ["CONFIRMED_IDENTITY_AMOUNT_CURRENCY_DATE"], "uncertainty": None}],
        "exceptions": [], "unresolved_transactions": [],
        "summary": {"orders": 1, "transactions": 1, "by_status": {"MATCHED": 1}, "matched_totals": {"RUB": "0.30"}}}
    return copy.deepcopy(result), copy.deepcopy([order]), copy.deepcopy([tx]), copy.deepcopy(rules)


def schema():
    return json.loads((Path(__file__).resolve().parents[2] / "tools/data_processing/reconciliation.schema.json").read_text())


def cluster_specimen():
    """Manual source cluster: O2's allocation does not remove T2 from O1."""
    result, orders, transactions, options = specimen()
    orders.append({**orders[0], "id": "O2", "amount": "0.20", "reference": "other"})
    transactions.append({**transactions[0], "id": "T2", "amount": "0.20"})
    options["allowed_types"].append("group")
    options["allocations"] = [{"order_id": "O2", "transaction_id": "T2", "amount": "0.20"}]
    second = copy.deepcopy(result["results"][0])
    second.update(source_index=1, order_id="O2", rule="group:explicit",
                  links=[{"transaction_id": "T2", "amount": "0.20"}], candidates=["T2"],
                  transaction_source_indices=[1], raw={"order": orders[1], "transactions": [transactions[1]]})
    second["normalized"].update(order_amount="0.20", payment_total="0.20")
    result["results"].append(second)
    first = result["results"][0]
    first.update(status="AMBIGUOUS", links=[], candidates=["T1", "T2"],
                 transaction_source_indices=[0, 1], reasons=["MULTIPLE_TRANSACTIONS"],
                 uncertainty="MULTIPLE_TRANSACTIONS")
    first["raw"]["transactions"] = copy.deepcopy(transactions)
    first["normalized"]["payment_total"] = "0"
    result["unresolved_transactions"] = [{"source_index": 0, "record_id": "T1",
        "reason": "UNALLOCATED_AMOUNT", "remaining_amount": "0.30", "raw": copy.deepcopy(transactions[0]),
        "normalized": {"amount": "0.30", "currency": "RUB", "date": "2026-01-10"}}]
    result["summary"] = {"orders": 2, "transactions": 2,
                         "by_status": {"AMBIGUOUS": 1, "MATCHED": 1}, "matched_totals": {"RUB": "0.20"}}
    return result, orders, transactions, options


class ValidationTests(unittest.TestCase):
    def test_multiple_source_payments_remain_ambiguous_without_partial(self):
        report = validate_result(*cluster_specimen(), schema=schema())
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["integrity_status"], "PASS")
        self.assertFalse(report["data_ready"])

    def test_forged_match_cannot_select_subset_of_full_or_trimmed_cluster(self):
        for trim in (False, True):
            with self.subTest(trim=trim):
                result, orders, transactions, options = cluster_specimen()
                row = result["results"][0]
                row.update(status="MATCHED", links=[{"transaction_id": "T1", "amount": "0.30"}],
                           reasons=["CONFIRMED_IDENTITY_AMOUNT_CURRENCY_DATE"], uncertainty=None)
                row["normalized"]["payment_total"] = "0.30"
                if trim:
                    row.update(candidates=["T1"], transaction_source_indices=[0])
                    row["raw"]["transactions"] = [copy.deepcopy(transactions[0])]
                result["unresolved_transactions"] = []
                result["summary"].update(by_status={"MATCHED": 2}, matched_totals={"RUB": "0.50"})
                report = validate_result(result, orders, transactions, options, schema=schema())
                self.assertEqual(report["integrity_status"], "FAIL")
                self.assertEqual(report["status"], "FAIL")
                self.assertFalse(report["data_ready"])

    def test_full_group_allocation_authorizes_the_same_source_cluster(self):
        from tools.data_processing.reconciliation import reconcile
        _, orders, transactions, options = cluster_specimen()
        options["allocations"].insert(0, {"order_id": "O1", "transaction_id": "T1", "amount": "0.30"})
        result = reconcile(orders, transactions, options)
        self.assertEqual([row["rule"] for row in result["results"]], ["group:explicit"] * 2)
        report = validate_result(result, orders, transactions, options, schema=schema())
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(report["data_ready"])

    def test_partial_links_must_cover_the_full_cluster(self):
        from tools.data_processing.reconciliation import reconcile
        _, orders, transactions, options = specimen()
        transactions.append({**transactions[0], "id": "T2", "amount": "0.20"})
        options["allowed_types"].append("partial")
        # A self-consistent forged MATCHED hides T2 in unresolved, with either trace.
        for trim in (False, True):
            with self.subTest(trim=trim):
                result, _, _, _ = specimen()
                result["summary"]["transactions"] = 2
                result["unresolved_transactions"] = [{"source_index": 1, "record_id": "T2",
                    "reason": "UNALLOCATED_AMOUNT", "remaining_amount": "0.20", "raw": copy.deepcopy(transactions[1]),
                    "normalized": {"amount": "0.20", "currency": "RUB", "date": "2026-01-10"}}]
                if not trim:
                    result["results"][0].update(candidates=["T1", "T2"], transaction_source_indices=[0, 1])
                    result["results"][0]["raw"]["transactions"] = copy.deepcopy(transactions)
                self.assertEqual(validate_result(result, orders, transactions, options, schema=schema())["integrity_status"], "FAIL")
        for amount, status in (("0.50", "MATCHED"), ("0.60", "REVIEW_REQUIRED")):
            with self.subTest(amount=amount):
                orders[0]["amount"] = amount
                result = reconcile(orders, transactions, options)
                self.assertEqual(result["results"][0]["status"], status)
                report = validate_result(result, orders, transactions, options, schema=schema())
                self.assertEqual(report["status"], "PASS")
                self.assertEqual(report["data_ready"], status == "MATCHED")

    def test_group_links_must_include_every_allocation_for_order(self):
        result, orders, transactions, options = specimen()
        transactions.append({**transactions[0], "id": "T2", "amount": "0.20"})
        options["allowed_types"].append("group")
        options["allocations"] = [{"order_id": "O1", "transaction_id": tx["id"], "amount": tx["amount"]}
                                  for tx in transactions]
        result["results"][0]["rule"] = "group:explicit"
        result["results"][0].update(candidates=["T1", "T2"], transaction_source_indices=[0, 1])
        result["results"][0]["raw"]["transactions"] = copy.deepcopy(transactions)
        result["summary"]["transactions"] = 2
        result["unresolved_transactions"] = [{"source_index": 1, "record_id": "T2", "reason": "UNALLOCATED_AMOUNT",
            "remaining_amount": "0.20", "raw": copy.deepcopy(transactions[1]),
            "normalized": {"amount": "0.20", "currency": "RUB", "date": "2026-01-10"}}]
        self.assertEqual(validate_result(result, orders, transactions, options, schema=schema())["integrity_status"], "FAIL")

    def test_single_exact_composite_and_signed_full_clusters_remain_valid(self):
        from tools.data_processing.reconciliation import reconcile
        for kind in ("exact", "composite", "refund", "signed_partial"):
            with self.subTest(kind=kind):
                _, orders, transactions, options = specimen()
                if kind == "composite":
                    options.update(keys=[["reference", "currency"]], allowed_types=["composite"], rule_order=["composite"])
                elif kind == "refund":
                    orders[0]["amount"] = transactions[0]["amount"] = "-0.30"
                    options["allowed_types"].append("refund")
                elif kind == "signed_partial":
                    orders[0]["amount"] = "0.20"
                    transactions.append({**transactions[0], "id": "T2", "amount": "-0.10"})
                    options["allowed_types"].extend(["partial", "refund"])
                report = validate_result(reconcile(orders, transactions, options), orders, transactions, options, schema=schema())
                self.assertEqual(report["status"], "PASS")
                self.assertTrue(report["data_ready"])

    def test_related_invalid_source_cannot_support_consuming_links(self):
        for source in ("orders", "transactions"):
            with self.subTest(source=source):
                result, orders, transactions, options = specimen()
                records = orders if source == "orders" else transactions
                records.append({**records[0], "id": "bad", "amount": "bad"})
                report = validate_result(result, orders, transactions, options, schema=schema())
                self.assertEqual(report["status"], "FAIL")
                self.assertTrue(any(e["check"] == "identity" for e in report["errors"]))

    def test_confirmed_match_cannot_bypass_key_order(self):
        from tools.data_processing.reconciliation import reconcile
        _, orders, transactions, options = cluster_specimen()
        options.pop("allocations")
        options["keys"].append(["id"])
        orders[0]["alternate"] = transactions[0]["alternate"] = "unique"
        options["keys"][1] = ["alternate"]
        forged = reconcile(orders, transactions, {**options, "keys": list(reversed(options["keys"]))})
        report = validate_result(forged, orders, transactions, options, schema=schema())
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(any(e["check"] == "priority" for e in report["errors"]))

    def test_confirmed_match_cannot_bypass_rule_priority(self):
        from tools.data_processing.reconciliation import reconcile
        _, orders, transactions, options = specimen()
        orders[0]["customer"] = "C1"; transactions[0]["customer"] = "C1"
        orders.append({**orders[0], "id": "O2", "customer": "C2"})
        transactions.append({**transactions[0], "id": "T2", "customer": "C2"})
        options["keys"].append(["customer", "currency"])
        options["allowed_types"].append("composite")
        options["rule_order"].append("composite")
        self.assertEqual(reconcile(orders, transactions, options)["results"][0]["status"], "AMBIGUOUS")
        reversed_options = {**options, "rule_order": ["composite", "exact"]}
        wrong_priority = reconcile(orders, transactions, reversed_options)
        self.assertEqual(wrong_priority["results"][0]["status"], "MATCHED")
        self.assertEqual(validate_result(wrong_priority, orders, transactions, options, schema=schema())["status"], "FAIL")

    def test_malformed_nested_result_types_produce_diagnostics(self):
        changes = [
            lambda r: r["results"][0].update(status=[]),
            lambda r: r["results"][0]["links"][0].update(transaction_id={}),
            lambda r: r.update(exceptions=[{"source": [], "source_index": 0}]),
            lambda r: r["results"][0].update(source_index=True),
        ]
        for change in changes:
            with self.subTest(change=change):
                inputs = specimen(); change(inputs[0])
                self.assertEqual(validate_result(*inputs)["status"], "FAIL")

    def test_independent_manual_example_has_consistent_invariants(self):
        result = validate_result(*specimen())
        self.assertEqual(result["integrity_status"], "PASS")
        self.assertEqual(result["errors"], [])

    def test_missing_confirmed_schema_blocks_ready(self):
        result = validate_result(*specimen())
        self.assertEqual(result["status"], "NOT_CHECKED")
        self.assertFalse(result["data_ready"])

    @unittest.skipUnless(importlib.util.find_spec("jsonschema"), "jsonschema not installed; dependency approval pending")
    def test_T12_json_schema_accepts_valid_and_rejects_wrong_types(self):
        inputs = specimen()
        self.assertEqual(validate_result(*inputs, schema=schema())["status"], "PASS")
        inputs[0]["results"][0]["source_index"] = "0"
        self.assertEqual(validate_result(*inputs, schema=schema())["status"], "FAIL")

    def test_invalid_json_duplicate_keys_and_nonfinite_values_fail(self):
        for raw in ('{"results":', '{"version":1,"version":2}', '{"value":NaN}'):
            _, orders, txs, options = specimen()
            with self.subTest(raw=raw):
                result = validate_result(raw, orders, txs, options)
                self.assertEqual(result["status"], "FAIL")

    def test_injected_critical_errors_are_detected(self):
        mutations = {
            "missing_order": lambda r: r["results"].clear(),
            "double_use": lambda r: r["results"][0]["links"].append({"transaction_id": "T1", "amount": "0.30"}),
            "wrong_sum": lambda r: r["summary"]["matched_totals"].update(RUB="0.31"),
            "wrong_row_sum": lambda r: r["results"][0]["normalized"].update(payment_total="0.29"),
            "wrong_allocation": lambda r: r["results"][0]["links"][0].update(amount="0.29"),
            "unknown_tx": lambda r: r["results"][0]["links"][0].update(transaction_id="missing"),
            "changed_source": lambda r: r["results"][0]["raw"]["order"].update(amount="999"),
            "wrong_currency": lambda r: r["results"][0]["normalized"].update(currency="USD"),
            "false_fuzzy_match": lambda r: r["results"][0].update(rule="fuzzy:reference"),
            "missing_reason": lambda r: r["results"][0].update(reasons=[]),
        }
        for name, change in mutations.items():
            with self.subTest(name=name):
                inputs = specimen(); change(inputs[0])
                report = validate_result(*inputs)
                self.assertEqual(report["status"], "FAIL")
                self.assertFalse(report["data_ready"])

    def test_wrong_source_identity_date_or_amount_is_not_supported_by_report(self):
        for field, value in (("reference", "different"), ("date", "2026-01-11"), ("amount", "0.31"), ("currency", "USD")):
            with self.subTest(field=field):
                inputs = specimen(); inputs[2][0][field] = value
                inputs[0]["results"][0]["raw"]["transactions"] = copy.deepcopy(inputs[2])
                report = validate_result(*inputs)
                self.assertEqual(report["status"], "FAIL")

    def test_source_records_not_covered_by_result_remain_unresolved(self):
        inputs = specimen()
        inputs[2].append({"id": "T2", "amount": "1.00", "currency": "RUB", "date": "2026-01-10", "reference": "other"})
        inputs[0]["summary"]["transactions"] = 2
        report = validate_result(*inputs)
        self.assertEqual(report["status"], "FAIL")
        inputs[0]["unresolved_transactions"] = [{"source_index": 1, "record_id": "T2", "reason": "UNALLOCATED_AMOUNT", "remaining_amount": "1.00",
            "raw": copy.deepcopy(inputs[2][1]), "normalized": {"amount": "1.00", "currency": "RUB", "date": "2026-01-10"}}]
        report = validate_result(*inputs)
        self.assertEqual(report["integrity_status"], "PASS")
        self.assertFalse(report["data_ready"])
        inputs[0]["unresolved_transactions"][0]["raw"]["amount"] = "9.00"
        self.assertEqual(validate_result(*inputs)["status"], "FAIL")

    def test_invalid_source_exception_cannot_disappear(self):
        from tools.data_processing.reconciliation import reconcile
        _, orders, transactions, options = specimen()
        orders[0]["amount"] = 0.3
        result = reconcile(orders, transactions, options)
        self.assertEqual(validate_result(result, orders, transactions, options)["integrity_status"], "PASS")
        result["exceptions"].clear()
        self.assertEqual(validate_result(result, orders, transactions, options)["status"], "FAIL")

    def test_group_and_partial_results_are_rechecked_from_sources(self):
        from tools.data_processing.reconciliation import reconcile
        _, orders, transactions, options = specimen()
        options["allowed_types"].extend(["group", "partial"])
        orders.append({**orders[0], "id": "O2", "amount": "0.20", "reference": "other"})
        orders[0]["amount"] = "0.10"
        options["allocations"] = [{"transaction_id": "T1", "order_id": "O1", "amount": "0.10"},
                                  {"transaction_id": "T1", "order_id": "O2", "amount": "0.20"}]
        result = reconcile(orders, transactions, options)
        self.assertEqual(validate_result(result, orders, transactions, options)["integrity_status"], "PASS")
        result["results"][0]["links"][0]["amount"] = "0.11"
        self.assertEqual(validate_result(result, orders, transactions, options)["status"], "FAIL")

    def test_remote_schema_references_are_refused_without_network(self):
        report = validate_result(*specimen(), schema={"$ref": "https://invalid.example/schema.json"})
        self.assertEqual(report["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
