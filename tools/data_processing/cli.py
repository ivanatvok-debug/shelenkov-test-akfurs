"""One project-local entrypoint for the four data Skills, not Q1–Q5."""

import argparse
import json
from pathlib import Path
import sys

from .quality import assess_quality
from .reconciliation import reconcile
from .safe_output import write_new_bytes
from .tabular import read_csv
from .validation import validate_result
from .xlsx import csv_to_xlsx, inspect_xlsx, read_xlsx, require_recalculation, write_xlsx, xlsx_to_csv


def load_json(path):
    def object_pairs(pairs):
        target = {}
        for key, value in pairs:
            if key in target:
                raise ValueError("Duplicate JSON key")
            target[key] = value
        return target

    def reject_constant(value):
        raise ValueError("Nonfinite JSON numbers are forbidden")

    return json.loads(Path(path).read_text(encoding="utf-8-sig"),
                      object_pairs_hook=object_pairs, parse_constant=reject_constant)


def _table(path, sheet=None, encoding=None, delimiter=None):
    suffix = Path(path).suffix.lower()
    if suffix == ".csv":
        return read_csv(path, encoding=encoding, delimiter=delimiter)
    if suffix == ".xlsx":
        return read_xlsx(path, sheet=sheet)
    raise ValueError("Expected .csv or .xlsx")


def _parser():
    parser = argparse.ArgumentParser(description="Project-local data Skills; no AK Furs answer calculation")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "quality", "csv-to-xlsx", "xlsx-to-csv", "require-recalculation"):
        cmd = commands.add_parser(name)
        cmd.add_argument("input")
        cmd.add_argument("--sheet")
        cmd.add_argument("--encoding")
        cmd.add_argument("--delimiter")
        cmd.add_argument("--output", required=name in {"csv-to-xlsx", "xlsx-to-csv"})
        if name == "quality":
            cmd.add_argument("--contract")
            cmd.add_argument("--other")
            cmd.add_argument("--other-sheet")
    cmd = commands.add_parser("create-xlsx")
    cmd.add_argument("--spec", required=True)
    cmd.add_argument("--output", required=True)
    for name in ("reconcile", "validate"):
        cmd = commands.add_parser(name)
        for option in ("orders", "transactions", "rules"):
            cmd.add_argument("--" + option, required=True)
        cmd.add_argument("--output")
        if name == "validate":
            cmd.add_argument("--result", required=True)
            cmd.add_argument("--schema", default=str(Path(__file__).with_name("reconciliation.schema.json")))
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        code, report_output = 0, True
        if args.command == "inspect":
            report = (inspect_xlsx(args.input) if Path(args.input).suffix.lower() == ".xlsx"
                      else _table(args.input, args.sheet, args.encoding, args.delimiter))
        elif args.command == "quality":
            table = _table(args.input, args.sheet, args.encoding, args.delimiter)
            other = _table(args.other, args.other_sheet) if args.other else None
            report = assess_quality(table, load_json(args.contract) if args.contract else None, other)
            code = 2 if report["status"] == "FAIL" else 3 if report["status"] == "NOT_CHECKED" else 0
        elif args.command in {"reconcile", "validate"}:
            orders, transactions, rules = (load_json(getattr(args, key)) for key in ("orders", "transactions", "rules"))
            if args.command == "reconcile":
                report = reconcile(orders, transactions, rules)
                if report["exceptions"] or report["unresolved_transactions"] or any(r["status"] != "MATCHED" for r in report["results"]):
                    code = 3
            else:
                report = validate_result(load_json(args.result), orders, transactions, rules, schema=load_json(args.schema))
                code = 2 if report["status"] == "FAIL" else 0 if report["data_ready"] else 3
        elif args.command == "csv-to-xlsx":
            report, report_output = csv_to_xlsx(args.input, args.output, args.encoding, args.delimiter), False
        elif args.command == "xlsx-to-csv":
            report, report_output = xlsx_to_csv(args.input, args.output, args.sheet, args.delimiter or ","), False
        elif args.command == "create-xlsx":
            report, report_output = write_xlsx(load_json(args.spec), args.output), False
        else:
            require_recalculation(args.input)
            raise RuntimeError("Recalculation was not verified")
        serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
        if report_output and args.output:
            output = Path(args.output)
            if output.suffix.lower() != ".json":
                raise ValueError("Machine report must use a .json output filename")
            if args.command in {"quality", "validate"} and output.with_suffix(".md").exists():
                raise FileExistsError("Readable summary already exists")
            write_new_bytes(output, serialized.encode("utf-8"))
            if args.command in {"quality", "validate"}:
                summary = report["summary"] + "\n\nScope: " + report["scope"] + "\n"
                write_new_bytes(output.with_suffix(".md"), summary.encode("utf-8"))
        sys.stdout.write(serialized)
        if code:
            sys.stderr.write("Checks are incomplete or blocking; see the structured report.\n")
        return code
    except Exception as exc:
        sys.stderr.write(json.dumps({"status": "FAIL", "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False) + "\n")
        return 2
