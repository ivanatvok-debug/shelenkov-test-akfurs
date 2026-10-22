"""Contract-driven diagnostics. Raw values are never replaced by a preview."""

from collections import Counter
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import re


def _empty(value):
    return value is None or isinstance(value, str) and not value.strip()


def _decimal(value, specification):
    if not isinstance(value, str) or not isinstance(specification, dict):
        raise ValueError("Money requires raw text and an explicit format")
    decimal = specification.get("decimal_separator")
    group = specification.get("thousands_separator")
    if decimal not in (".", ",") or group not in (None, " ", ",", ".") or decimal == group:
        raise ValueError("Unsupported or ambiguous decimal/group separators")
    signless = value[1:] if value[:1] in ("+", "-") else value
    parts = signless.split(decimal)
    if len(parts) > 2 or len(parts) == 2 and not re.fullmatch(r"[0-9]+", parts[1]):
        raise ValueError("Invalid fraction")
    integer = parts[0]
    if group and group in integer:
        groups = integer.split(group)
        if not re.fullmatch(r"[0-9]{1,3}", groups[0]) or not all(re.fullmatch(r"[0-9]{3}", g) for g in groups[1:]):
            raise ValueError("Invalid thousands grouping")
        integer = "".join(groups)
    if not re.fullmatch(r"[0-9]+", integer):
        raise ValueError("Invalid numeric format")
    sign = value[0] if value[:1] in ("+", "-") else ""
    normalized = sign + integer + ("." + parts[1] if len(parts) == 2 else "")
    amount = Decimal(normalized)
    scale = specification.get("max_decimal_places")
    if scale is not None:
        if type(scale) is not int or scale < 0:
            raise ValueError("Invalid max_decimal_places")
        if -amount.as_tuple().exponent > scale:
            raise ValueError("Precision exceeds the explicit contract; no rounding performed")
    return format(amount, "f")


def _observed(value):
    if _empty(value):
        return "missing"
    if not isinstance(value, str):
        return type(value).__name__
    if re.fullmatch(r"0[0-9]+", value):
        return "identifier_like"
    if re.fullmatch(r"[+-]?[0-9]+", value):
        return "integer"
    if re.fullmatch(r"[+-]?[0-9]+\.[0-9]+", value):
        return "decimal"
    try:
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            datetime.strptime(value, "%Y-%m-%d")
            return "date"
    except ValueError:
        pass
    return "string"


def _validate_contract(contract):
    if not isinstance(contract, dict):
        raise ValueError("Quality contract must be an object or None")
    for key in ("required", "unique", "join_keys"):
        if key in contract:
            fields = contract[key]
            if (not isinstance(fields, list) or not all(isinstance(f, str) and f for f in fields)
                    or len(set(fields)) != len(fields)):
                raise ValueError(key + " must contain distinct field names")
    if "types" in contract:
        if not isinstance(contract["types"], dict) or not all(
                isinstance(k, str) and isinstance(v, str) and v in {"string", "identifier", "integer", "decimal", "money", "date"}
                for k, v in contract["types"].items()):
            raise ValueError("Unsupported type contract")
    if "allowed_values" in contract and (not isinstance(contract["allowed_values"], dict)
            or not all(isinstance(k, str) and isinstance(v, list) for k, v in contract["allowed_values"].items())):
        raise ValueError("allowed_values must map fields to lists")
    if "date_formats" in contract and (not isinstance(contract["date_formats"], list)
            or not all(isinstance(v, str) and v for v in contract["date_formats"])):
        raise ValueError("date_formats must be a list of formats")
    if "money_format" in contract:
        _decimal("0", contract["money_format"])


def assess_quality(table: dict, contract: dict | None, other_table: dict | None = None) -> dict:
    if not isinstance(table, dict) or not isinstance(table.get("headers"), list) or not isinstance(table.get("rows"), list):
        raise ValueError("Table requires headers and rows")
    headers, rows = table["headers"], table["rows"]
    if (not all(isinstance(h, str) for h in headers)
            or not all(isinstance(r, list) and all(v is None or isinstance(v, (str, int, float, bool)) for v in r) for r in rows)):
        raise ValueError("Table cells must be scalar values")
    if contract is not None:
        _validate_contract(contract)
    spec = contract or {}
    numbers = table.get("source_row_numbers", list(range(2, len(rows) + 2)))
    if len(numbers) != len(rows):
        raise ValueError("Source row number coverage is inconsistent")
    checks, normalized = [], deepcopy(rows)

    def check(rule, status, field=None, source_rows=None, details=""):
        checks.append({"rule": rule, "status": status, "field": field,
                       "source_rows": source_rows or [], "details": details})

    if not headers or any(not h.strip() for h in headers) or len(set(headers)) != len(headers):
        check("structure", "FAIL", details="Missing, empty or duplicate headers")
    malformed = [numbers[i] for i, row in enumerate(rows) if row and len(row) != len(headers)]
    check("row_width", "FAIL" if malformed else "PASS", source_rows=malformed)
    blank_rows = [numbers[i] for i, row in enumerate(rows) if not row or all(_empty(v) for v in row)]
    empty_columns, missing, analysis = [], {}, {}
    duplicates = len(rows) - len({tuple(row) for row in rows})
    if blank_rows:
        check("empty_rows", "WARNING", source_rows=blank_rows)
    if duplicates:
        check("duplicate_rows", "WARNING", details=str(duplicates))
    for column, field in enumerate(headers):
        values = [row[column] if column < len(row) else None for row in rows]
        missing[field] = sum(_empty(v) for v in values)
        if values and all(_empty(v) for v in values):
            empty_columns.append(field)
            check("empty_column", "WARNING", field)
        elif missing[field]:
            check("missing_values", "WARNING", field,
                  [numbers[i] for i, v in enumerate(values) if _empty(v)])
        observed = sorted({_observed(v) for v in values})
        analysis[field] = {"observed_types": observed,
                           "uncertain": len(set(observed) - {"missing"}) != 1 or "identifier_like" in observed}

    for name in ("required", "unique", "types", "allowed_values", "join_keys"):
        if name not in spec:
            check("join" if name == "join_keys" else name, "NOT_CHECKED", details="Not supplied in contract")
    for field in spec.get("required", []):
        if field not in headers:
            check("required", "FAIL", field, details="Column absent")
            continue
        col = headers.index(field)
        absent = [numbers[i] for i, row in enumerate(rows) if len(row) <= col or _empty(row[col])]
        check("required", "FAIL" if absent else "PASS", field, absent)
    for field in spec.get("unique", []):
        if field not in headers:
            check("unique", "FAIL", field, details="Column absent")
            continue
        col = headers.index(field)
        values = Counter(row[col] for row in rows if len(row) > col and not _empty(row[col]))
        repeated = [numbers[i] for i, row in enumerate(rows) if len(row) > col and values[row[col]] > 1]
        check("unique", "FAIL" if repeated else "PASS", field, repeated)
    for field, accepted in spec.get("allowed_values", {}).items():
        if field not in headers:
            check("allowed_values", "FAIL", field, details="Column absent")
            continue
        col = headers.index(field)
        invalid = [numbers[i] for i, row in enumerate(rows) if len(row) > col
                   and not _empty(row[col]) and row[col] not in accepted]
        check("allowed_values", "FAIL" if invalid else "PASS", field, invalid)
    for field, kind in spec.get("types", {}).items():
        if field not in headers:
            check("types", "FAIL", field, details="Column absent")
            continue
        col = headers.index(field)
        invalid, uncertain = [], []
        missing_format = (kind in {"decimal", "money"} and "money_format" not in spec
                          or kind == "date" and not spec.get("date_formats"))
        for i, row in enumerate(rows):
            if len(row) <= col or _empty(row[col]):
                continue
            value = row[col]
            try:
                if kind in {"string", "identifier"}:
                    if not isinstance(value, str):
                        raise ValueError("String required")
                elif kind == "integer":
                    if not isinstance(value, str) or not re.fullmatch(r"[+-]?[0-9]+", value):
                        raise ValueError("Integer text required")
                elif kind in {"decimal", "money"}:
                    if "money_format" not in spec:
                        uncertain.append(numbers[i]); continue
                    normalized[i][col] = _decimal(value, spec["money_format"])
                elif kind == "date":
                    if not spec.get("date_formats"):
                        uncertain.append(numbers[i]); continue
                    dates = set()
                    for fmt in spec["date_formats"]:
                        try:
                            dates.add(datetime.strptime(value, fmt).date().isoformat())
                        except (ValueError, TypeError):
                            pass
                    if not dates:
                        raise ValueError("No allowed date format")
                    if len(dates) > 1:
                        uncertain.append(numbers[i]); continue
                    normalized[i][col] = dates.pop()
            except (ValueError, TypeError):
                invalid.append(numbers[i])
        check("types", "FAIL" if invalid else "NOT_CHECKED" if uncertain or missing_format else "PASS",
              field, invalid or uncertain, "Ambiguous or missing format" if uncertain or missing_format else kind)

    join = None
    if spec.get("join_keys"):
        fields = spec["join_keys"]
        if other_table is None:
            check("join", "NOT_CHECKED", details="Other source not supplied")
        elif (not isinstance(other_table, dict) or not isinstance(other_table.get("headers"), list)
              or not all(isinstance(h, str) and h.strip() for h in other_table["headers"])
              or len(set(other_table["headers"])) != len(other_table["headers"])
              or not isinstance(other_table.get("rows"), list)
              or not all(isinstance(r, list) and len(r) == len(other_table["headers"])
                         and all(v is None or isinstance(v, (str, int, float, bool)) for v in r)
                         for r in other_table["rows"])):
            check("join", "FAIL", details="Other source has ambiguous or malformed structure")
        elif any(f not in headers or f not in other_table.get("headers", []) for f in fields):
            check("join", "FAIL", details="Join column absent")
        else:
            left_cols = [headers.index(f) for f in fields]
            right_cols = [other_table["headers"].index(f) for f in fields]

            def key(row, columns):
                return tuple(row[c] for c in columns) if all(c < len(row) and not _empty(row[c]) for c in columns) else None

            right_counts = Counter(key(row, right_cols) for row in other_table["rows"])
            right_counts.pop(None, None)
            matches = [right_counts[key(row, left_cols)] for row in rows]
            join = {"keys": list(fields), "unmatched_left_rows": [numbers[i] for i, n in enumerate(matches) if n == 0],
                    "multiplying_left_rows": [numbers[i] for i, n in enumerate(matches) if n > 1],
                    "predicted_inner_rows": sum(matches)}
            loss_or_expansion = any(n != 1 for n in matches)
            check("join", "WARNING" if loss_or_expansion else "PASS", details="Exact raw-key inner join prediction")
    elif "join_keys" in spec:
        check("join", "PASS", details="Explicitly no join requested; no claim about a future join")

    blockers = [c for c in checks if c["status"] == "FAIL"]
    warnings = [c for c in checks if c["status"] == "WARNING"]
    not_checked = [c for c in checks if c["status"] == "NOT_CHECKED"]
    status = "FAIL" if blockers else "NOT_CHECKED" if not_checked else "WARNING" if warnings else "PASS"
    return {"status": status, "scope": "Specified quality contract; no automatic cleaning or join",
            "checks": checks, "blockers": blockers, "warnings": warnings, "not_checked": not_checked,
            "statistics": {"input_rows": len(rows), "output_rows": len(normalized),
                           "empty_rows": len(blank_rows), "empty_columns": empty_columns,
                           "duplicate_rows": duplicates, "missing_values": missing},
            "type_analysis": analysis, "raw_rows": deepcopy(rows), "normalized_rows": normalized,
            "join": join, "summary": f"{status}: {len(rows)} input rows; {len(blockers)} blockers; "
            f"{len(warnings)} warnings; {len(not_checked)} checks unavailable"}
