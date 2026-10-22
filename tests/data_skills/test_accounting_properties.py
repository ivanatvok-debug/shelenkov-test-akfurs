"""Forward-review cases retained as reproducible regression checks."""

from fractions import Fraction
import itertools
import random
import unittest

from tools.data_processing.reconciliation import reconcile


def record(identifier, amount, reference):
    return {"id": identifier, "amount": amount, "currency": "RUB",
            "date": "2026-01-01", "reference": reference}


def rules():
    return {"keys": [["reference"]], "currencies": ["RUB"], "amount_tolerance": "0",
            "date_tolerance_days": 0, "allowed_types": ["exact", "partial", "refund"],
            "rule_order": ["exact"]}


class AccountingProperties(unittest.TestCase):
    def test_250_seeded_source_balances_and_unique_transaction_use(self):
        rng = random.Random(7231)
        for _ in range(250):
            orders = [record("O" + str(i), str(rng.randint(-200, 200)), str(i)) for i in range(rng.randint(0, 5))]
            transactions = [record("T" + str(i), str(rng.randint(-200, 200)), str(rng.randint(0, 6))) for i in range(rng.randint(0, 10))]
            result = reconcile(orders, transactions, rules())
            self.assertEqual(len(result["results"]), len(orders))
            for tx in transactions:
                used = [Fraction(link["amount"]) for row in result["results"] for link in row["links"] if link["transaction_id"] == tx["id"]]
                residual = [u for u in result["unresolved_transactions"] if u["record_id"] == tx["id"]]
                remaining = Fraction(residual[0]["remaining_amount"]) if residual else Fraction(0)
                self.assertEqual(sum(used, Fraction(0)) + remaining, Fraction(tx["amount"]))
                self.assertLessEqual(len(used), 1)

    def test_36_input_permutations_preserve_financial_decisions(self):
        orders = [record("O1", "100", "A"), record("O2", "40", "B"), record("O3", "90", "C")]
        transactions = [record("T1", "60", "A"), record("T2", "40", "A"), record("T3", "40", "B")]

        def decisions(result):
            return {r["order_id"]: (r["status"], sorted((link["transaction_id"], link["amount"]) for link in r["links"]),
                                    r["normalized"].get("payment_total")) for r in result["results"]}

        expected = decisions(reconcile(orders, transactions, rules()))
        for left in itertools.permutations(orders):
            for right in itertools.permutations(transactions):
                self.assertEqual(decisions(reconcile(list(left), list(right), rules())), expected)


if __name__ == "__main__":
    unittest.main()
