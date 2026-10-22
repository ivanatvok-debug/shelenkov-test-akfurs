import copy
from pathlib import Path
import tempfile
import unittest

from tools.data_processing.tabular import FormatUncertain, read_csv
from tools.data_processing.quality import assess_quality


def contract(**extra):
    return {"required": ["id", "amount", "date"], "unique": ["id"],
            "types": {"id": "identifier", "amount": "money", "date": "date"},
            "allowed_values": {}, "date_formats": ["%Y-%m-%d"],
            "money_format": {"decimal_separator": ".", "thousands_separator": None},
            "join_keys": [], **extra}


def table(rows, headers=None):
    return {"format": "csv", "headers": headers or ["id", "amount", "date"],
            "rows": rows, "source_row_numbers": list(range(2, len(rows) + 2))}


class QualityTests(unittest.TestCase):
    def test_multiple_money_signs_are_rejected(self):
        for value in ("+-100.00", "--100.00", "-+100.00"):
            with self.subTest(value=value):
                self.assertEqual(assess_quality(table([["001", value, "2026-01-01"]]), contract())["status"], "FAIL")

    def test_missing_and_invalid_money_formats_do_not_pass_empty_input(self):
        spec = contract(); del spec["money_format"]
        self.assertEqual(assess_quality(table([]), spec)["status"], "NOT_CHECKED")
        with self.assertRaises(ValueError):
            assess_quality(table([]), contract(money_format={"decimal_separator": "bad"}))

    def test_empty_source_row_does_not_bypass_required_fields(self):
        self.assertEqual(assess_quality(table([[]]), contract())["status"], "FAIL")

    def test_join_rejects_ambiguous_right_headers(self):
        right = table([["001", "002"]], headers=["id", "id"])
        result = assess_quality(table([["001", "1.00", "2026-01-01"]]), contract(join_keys=["id"]), right)
        self.assertEqual(result["status"], "FAIL")

    def test_bad_type_configuration_has_a_clear_error(self):
        with self.assertRaises(ValueError):
            assess_quality(table([]), contract(types={"id": []}))

    def test_join_result_does_not_alias_the_input_contract(self):
        spec = contract(join_keys=["id"])
        source = table([["001", "1.00", "2026-01-01"]])
        result = assess_quality(source, spec, source)
        result["join"]["keys"].append("changed")
        self.assertEqual(spec["join_keys"], ["id"])

    def test_two_structurally_possible_delimiters_require_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ambiguous.csv"
            path.write_text("id;name,amount\n001;Alpha,100\n", encoding="utf-8")
            with self.assertRaises(FormatUncertain):
                read_csv(path)
            self.assertEqual(read_csv(path, delimiter=";")["headers"], ["id", "name,amount"])

    def test_T01_utf8_bom_and_cyrillic_preserve_raw_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "synthetic.csv"
            raw = "id,имя\n0001,Синтетика\n".encode("utf-8-sig")
            path.write_bytes(raw)
            result = read_csv(path)
            self.assertEqual(result["encoding"], "utf-8-sig")
            self.assertEqual(result["headers"], ["id", "имя"])
            self.assertEqual(result["rows"], [["0001", "Синтетика"]])
            self.assertEqual(path.read_bytes(), raw)

    def test_T02_alternative_delimiter_and_quoted_separator(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "synthetic.csv"
            path.write_text('id;description;amount\n0001;"a;b";10,20\n', encoding="utf-8")
            result = read_csv(path)
            self.assertEqual(result["delimiter"], ";")
            self.assertEqual(result["rows"], [["0001", "a;b", "10,20"]])

    def test_encoding_uncertainty_requires_explicit_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "synthetic.csv"
            path.write_bytes("id;имя\n001;Синтетика\n".encode("cp1251"))
            with self.assertRaises(FormatUncertain):
                read_csv(path)
            self.assertEqual(read_csv(path, encoding="cp1251")["rows"][0][0], "001")

    def test_T05_duplicate_ids_fail_and_duplicate_rows_are_reported(self):
        result = assess_quality(table([["001", "1.20", "2026-01-01"]] * 2), contract())
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any(c["rule"] == "unique" for c in result["blockers"]))
        self.assertEqual(result["statistics"]["duplicate_rows"], 1)

    def test_T06_missing_required_field_and_empty_value_fail(self):
        result = assess_quality(table([["001", "2026-01-01"]], headers=["id", "date"]), contract())
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any(c["rule"] == "required" and c["field"] == "amount" for c in result["blockers"]))
        blank = assess_quality(table([["001", "", "2026-01-01"]]), contract())
        self.assertEqual(blank["status"], "FAIL")

    def test_blank_rows_columns_and_missing_values_are_counted(self):
        result = assess_quality(table([["001", "1.00", "2026-01-01", ""], []],
                                      headers=["id", "amount", "date", "empty"]), contract())
        self.assertEqual(result["statistics"]["input_rows"], 2)
        self.assertEqual(result["statistics"]["empty_rows"], 1)
        self.assertEqual(result["statistics"]["empty_columns"], ["empty"])
        self.assertEqual(result["statistics"]["output_rows"], 2)
        self.assertEqual(result["raw_rows"][-1], [])

    def test_explicit_money_format_has_separate_normalized_values(self):
        result = assess_quality(table([["0001", "1 234,56", "2026-01-01"]]),
                                contract(money_format={"decimal_separator": ",", "thousands_separator": " "}))
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["raw_rows"][0][1], "1 234,56")
        self.assertEqual(result["normalized_rows"][0][1], "1234.56")
        self.assertEqual(result["normalized_rows"][0][0], "0001")

    def test_ambiguous_money_and_dates_are_not_silently_coerced(self):
        result = assess_quality(table([["001", "1,234", "01/02/2026"]]), contract())
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["raw_rows"][0][1:], ["1,234", "01/02/2026"])
        self.assertGreaterEqual(len(result["blockers"]), 2)

    def test_invalid_money_grouping_is_rejected(self):
        result = assess_quality(table([["001", "12 34,56", "2026-01-01"]]),
                                contract(money_format={"decimal_separator": ",", "thousands_separator": " "}))
        self.assertEqual(result["status"], "FAIL")

    def test_invalid_calendar_date_and_disallowed_value_fail(self):
        result = assess_quality(table([["001", "1.00", "2026-02-30"]]),
                                contract(allowed_values={"id": ["002"]}))
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any(c["rule"] == "allowed_values" for c in result["blockers"]))

    def test_join_predicts_record_loss_and_multiplication(self):
        left = table([["001", "1.00", "2026-01-01"], ["002", "2.00", "2026-01-01"]])
        right = table([["001", "1.00", "2026-01-01"], ["001", "1.00", "2026-01-01"]])
        result = assess_quality(left, contract(join_keys=["id"]), right)
        self.assertEqual(result["join"]["unmatched_left_rows"], [3])
        self.assertEqual(result["join"]["multiplying_left_rows"], [2])
        self.assertEqual(result["join"]["predicted_inner_rows"], 2)
        self.assertEqual(result["status"], "WARNING")

    def test_join_without_other_source_is_not_checked(self):
        result = assess_quality(table([["001", "1.00", "2026-01-01"]]), contract(join_keys=["id"]))
        self.assertEqual(result["status"], "NOT_CHECKED")
        self.assertTrue(any(c["rule"] == "join" for c in result["not_checked"]))

    def test_no_contract_does_not_establish_quality_pass(self):
        result = assess_quality(table([["001", "1.00", "2026-01-01"]]), None)
        self.assertEqual(result["status"], "NOT_CHECKED")

    def test_duplicate_headers_and_ragged_rows_block_structural_readiness(self):
        duplicate = assess_quality(table([["a", "b"]], headers=["id", "id"]), contract())
        ragged = assess_quality(table([["001", "1.00", "2026-01-01", "extra"]]), contract())
        self.assertEqual(duplicate["status"], "FAIL")
        self.assertEqual(ragged["status"], "FAIL")

    def test_assessment_does_not_mutate_source(self):
        source = table([["001", "1.00", "2026-01-01"]]); before = copy.deepcopy(source)
        assess_quality(source, contract())
        self.assertEqual(source, before)


if __name__ == "__main__":
    unittest.main()
