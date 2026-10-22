import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.data_processing.safe_output import OUTPUT_ROOT, PROJECT_ROOT


class CliTests(unittest.TestCase):
    def setUp(self):
        OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix="synthetic-cli-", dir=OUTPUT_ROOT)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "tools.data_processing", *map(str, args)],
                              cwd=PROJECT_ROOT, text=True, capture_output=True)

    def json_file(self, name, data):
        path = self.root / name; path.write_text(json.dumps(data), encoding="utf-8"); return path

    def inputs(self):
        orders = [{"id": "O1", "amount": "0.30", "currency": "RUB", "date": "2026-01-10", "ref": "0001"}]
        transactions = [{**orders[0], "id": "T1"}]
        rules = {"keys": [["ref"]], "currencies": ["RUB"], "amount_tolerance": "0",
                 "date_tolerance_days": 0, "allowed_types": ["exact"], "rule_order": ["exact"]}
        return (self.json_file("orders.json", orders), self.json_file("transactions.json", transactions),
                self.json_file("rules.json", rules))

    def test_reconciliation_and_independent_validation_commands(self):
        orders, transactions, rules = self.inputs(); output = self.root / "reconciliation.json"
        run = self.run_cli("reconcile", "--orders", orders, "--transactions", transactions, "--rules", rules, "--output", output)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)["results"][0]["status"], "MATCHED")
        report = self.root / "VALIDATION_REPORT.json"
        run = self.run_cli("validate", "--result", output, "--orders", orders, "--transactions", transactions,
                           "--rules", rules, "--output", report)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)["status"], "PASS")
        self.assertTrue(json.loads(report.read_text())["data_ready"])
        self.assertTrue(report.with_suffix(".md").exists())

    def test_quality_outputs_machine_report_and_readable_summary(self):
        source = self.root / "synthetic.csv"; source.write_text("id,amount\n0001,0.30\n", encoding="utf-8-sig")
        contract = self.json_file("quality-contract.json", {"required": ["id", "amount"], "unique": ["id"],
            "types": {"id": "identifier", "amount": "money"}, "allowed_values": {}, "join_keys": [],
            "money_format": {"decimal_separator": ".", "thousands_separator": None}})
        output = self.root / "quality.json"
        run = self.run_cli("quality", source, "--contract", contract, "--output", output)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)["status"], "PASS")
        self.assertTrue(output.with_suffix(".md").exists())

    def test_unmatched_is_preserved_and_has_nonzero_exit(self):
        orders, transactions, rules = self.inputs()
        rows = json.loads(transactions.read_text()); rows[0]["ref"] = "other"
        transactions.write_text(json.dumps(rows))
        output = self.root / "unresolved.json"
        run = self.run_cli("reconcile", "--orders", orders, "--transactions", transactions, "--rules", rules, "--output", output)
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(json.loads(run.stdout)["results"][0]["status"], "UNMATCHED")
        self.assertEqual(len(json.loads(output.read_text())["unresolved_transactions"]), 1)

    def test_new_workbook_and_both_conversion_commands_work(self):
        source = self.root / "synthetic.csv"; source.write_text("id,amount\n0001,0.30\n")
        output = self.root / "converted.xlsx"
        self.assertEqual(self.run_cli("csv-to-xlsx", source, "--output", output).returncode, 0)
        read = self.run_cli("inspect", output)
        self.assertEqual(json.loads(read.stdout)["sheets"][0]["rows"][1], ["0001", "0.30"])
        self.assertEqual(self.run_cli("xlsx-to-csv", output, "--output", self.root / "back.csv").returncode, 0)
        spec = self.json_file("new-book.json", {"Data": [["id"], ["0001"]]})
        self.assertEqual(self.run_cli("create-xlsx", "--spec", spec, "--output", self.root / "new.xlsx").returncode, 0)

    def test_errors_are_stderr_only_and_no_outputs_are_overwritten(self):
        source = self.root / "synthetic.csv"; source.write_text("id\n0001\n")
        output = self.root / "existing.json"; output.write_text("unchanged")
        run = self.run_cli("inspect", source, "--output", output)
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(run.stdout, "")
        self.assertTrue(run.stderr)
        self.assertEqual(json.loads(run.stderr)["status"], "FAIL")
        self.assertEqual(output.read_text(), "unchanged")
        run = self.run_cli("inspect", source, "--output", PROJECT_ROOT / "escaped.json")
        self.assertNotEqual(run.returncode, 0)
        self.assertFalse((PROJECT_ROOT / "escaped.json").exists())


if __name__ == "__main__":
    unittest.main()
