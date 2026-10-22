"""Independent result checks using rational arithmetic, without importing matcher.

JSON Schema is an additional gate. Its absence never becomes PASS. This module
checks this tool's reconciliation protocol, not the employer's answer template.
"""

from collections import Counter, defaultdict
from datetime import date
from difflib import SequenceMatcher
from fractions import Fraction
import json
import re
import unicodedata


def _number(value):
    if not isinstance(value, str) or not re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?", value):
        raise ValueError("Expected an exact decimal string")
    return Fraction(value)


def _identity(raw, fields):
    if not all(isinstance(raw.get(f), str) and raw[f].strip() for f in fields):
        return None
    return tuple(raw[f] for f in fields)


def _first_rule(raw, transactions, valid_transaction_indices, rules):
    """Check search precedence from source identities, without running matcher."""
    def normalized(value):
        return " ".join(unicodedata.normalize("NFKC", value).casefold().split()) if isinstance(value, str) else ""

    for kind in rules["rule_order"]:
        if kind in {"exact", "composite"}:
            for fields in rules["keys"]:
                if (len(fields) == 1) != (kind == "exact"):
                    continue
                key = _identity(raw, fields)
                if key is not None and any(_identity(source, fields) == key for source in transactions):
                    return kind + ":" + ",".join(fields)
        else:
            value = normalized(raw.get(rules["candidate_field"]))
            if value:
                for index in valid_transaction_indices:
                    other = normalized(transactions[index].get(rules["candidate_field"]))
                    if other and (value == other if kind == "normalized" else
                                  SequenceMatcher(None, value, other, autojunk=False).ratio() >= float(rules["fuzzy_threshold"])):
                        return kind + ":" + rules["candidate_field"]
    return None


def _source_values(records, currencies):
    counts = Counter(r.get("id") for r in records if isinstance(r.get("id"), str))
    values, invalid = {}, set()
    for i, raw in enumerate(records):
        try:
            identifier = raw.get("id")
            if not isinstance(identifier, str) or not identifier.strip() or counts[identifier] != 1:
                raise ValueError("Invalid identity")
            amount = _number(raw.get("amount"))
            currency = raw.get("currency")
            if not isinstance(currency, str) or currency not in currencies:
                raise ValueError("Invalid currency")
            raw_date = raw.get("date")
            if not isinstance(raw_date, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw_date):
                raise ValueError("Invalid date")
            values[i] = (identifier, amount, currency, date.fromisoformat(raw_date))
        except (ValueError, TypeError):
            invalid.add(i)
    return values, invalid


def _remote_reference(value):
    if isinstance(value, dict):
        for name, child in value.items():
            if name in {"$ref", "$dynamicRef"} and (not isinstance(child, str) or not child.startswith("#")):
                return True
            if _remote_reference(child):
                return True
    elif isinstance(value, list):
        return any(_remote_reference(child) for child in value)
    return False


def validate_result(result, orders: list[dict], transactions: list[dict], rules: dict,
                    schema: dict | None = None) -> dict:
    errors, checks = [], []

    def error(check, message):
        errors.append({"check": check, "message": message})

    def finish():
        integrity = "FAIL" if any(e["check"] != "schema" for e in errors) else "PASS"
        status = "FAIL" if errors else "NOT_CHECKED" if any(c["status"] == "NOT_CHECKED" for c in checks) else "PASS"
        ready = (status == "PASS" and isinstance(result, dict)
                 and not result.get("exceptions") and not result.get("unresolved_transactions")
                 and all(r.get("status") == "MATCHED" for r in result.get("results", [])))
        return {"status": status, "integrity_status": integrity, "data_ready": ready,
                "scope": "Reconciliation protocol and independently recomputed source invariants; not AK Furs answers",
                "checks": checks, "errors": errors,
                "summary": f"{status}: {len(errors)} errors; financial readiness={ready}"}

    def unique_object(pairs):
        target = {}
        for key, value in pairs:
            if key in target:
                raise ValueError("Duplicate JSON key")
            target[key] = value
        return target

    try:
        if isinstance(result, str):
            result = json.loads(result, object_pairs_hook=unique_object,
                                parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON number")))
        json.dumps(result, allow_nan=False)
        if not isinstance(result, dict):
            raise ValueError("Expected a result object")
        checks.append({"check": "json", "status": "PASS"})
    except (ValueError, TypeError, RecursionError):
        error("json", "Malformed, duplicated-key, nonfinite or non-object JSON")
        return finish()

    if schema is None:
        checks.append({"check": "schema", "status": "NOT_CHECKED", "reason": "No confirmed schema supplied"})
    elif not isinstance(schema, dict) or _remote_reference(schema):
        error("schema", "Schema must be an object with only local fragment references; no remote resolution")
    else:
        try:
            from jsonschema import Draft202012Validator, FormatChecker
        except ImportError:
            checks.append({"check": "schema", "status": "NOT_CHECKED", "reason": "jsonschema unavailable"})
        else:
            try:
                Draft202012Validator.check_schema(schema)
                issues = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(result))
                for issue in issues:
                    error("schema", "Violation at " + "/".join(map(str, issue.absolute_path)))
                checks.append({"check": "schema", "status": "FAIL" if issues else "PASS"})
            except Exception as exc:
                # Third-party schema/ref errors are diagnostic failures, never a readiness success.
                error("schema", "Schema could not be checked: " + type(exc).__name__)

    try:
        if (not isinstance(orders, list) or not isinstance(transactions, list)
                or not all(isinstance(r, dict) for r in orders + transactions)):
            raise ValueError("Invalid sources")
        if not isinstance(rules, dict):
            raise ValueError("Invalid rules")
        tolerance = _number(rules["amount_tolerance"])
        days, currencies = rules["date_tolerance_days"], rules["currencies"]
        allowed, keys, search = rules["allowed_types"], rules["keys"], rules["rule_order"]
        if tolerance < 0 or type(days) is not int or days < 0:
            raise ValueError("Invalid tolerance")
        if not isinstance(currencies, list) or not currencies or not all(isinstance(c, str) for c in currencies):
            raise ValueError("Invalid currencies")
        if (not isinstance(allowed, list) or not all(isinstance(t, str) for t in allowed)
                or not set(allowed) <= {"exact", "composite", "normalized", "fuzzy", "partial", "group", "refund"}
                or not isinstance(search, list) or not all(isinstance(t, str) for t in search)
                or set(search) != set(allowed) & {"exact", "composite", "normalized", "fuzzy"}):
            raise ValueError("Invalid rule types")
        if not isinstance(keys, list) or not all(isinstance(k, list) and k and all(isinstance(f, str) and f for f in k) for k in keys):
            raise ValueError("Invalid keys")
        if set(search) & {"normalized", "fuzzy"}:
            if not isinstance(rules.get("candidate_field"), str) or not rules["candidate_field"]:
                raise ValueError("Missing candidate field")
        if "fuzzy" in search and not 0 < _number(rules.get("fuzzy_threshold")) <= 1:
            raise ValueError("Invalid fuzzy threshold")
        ov, invalid_orders = _source_values(orders, currencies)
        tv, invalid_transactions = _source_values(transactions, currencies)
    except (ValueError, TypeError, KeyError):
        error("configuration", "Cannot independently interpret source data or explicit rules")
        return finish()

    rows, unresolved, exceptions, summary = (result.get(k) for k in ("results", "unresolved_transactions", "exceptions", "summary"))
    if (result.get("version") != "1.0" or not isinstance(rows, list)
            or not all(isinstance(r, dict) for r in rows) or not isinstance(unresolved, list)
            or not all(isinstance(r, dict) for r in unresolved) or not isinstance(exceptions, list)
            or not all(isinstance(r, dict) for r in exceptions) or not isinstance(summary, dict)):
        error("structure", "Missing or invalid protocol collections/version")
        return finish()
    indexes = [r.get("source_index") for r in rows]
    if not all(type(i) is int for i in indexes) or sorted(indexes) != list(range(len(orders))):
        error("coverage", "Every source order must occur exactly once by source_index")

    tx_ids = {value[0]: index for index, value in tv.items()}
    order_ids = {value[0]: index for index, value in ov.items()}
    explicit, allocations_by_tx = {}, defaultdict(list)
    try:
        for allocation in rules.get("allocations", []):
            oi = order_ids[allocation["order_id"]]
            ti = tx_ids[allocation["transaction_id"]]
            pair = (oi, ti)
            amount = _number(allocation["amount"])
            if pair in explicit or not amount or (amount > 0) != (tv[ti][1] > 0) or ov[oi][2] != tv[ti][2]:
                raise ValueError("Invalid allocation")
            explicit[pair] = amount
            allocations_by_tx[ti].append(amount)
        if explicit and "group" not in allowed:
            raise ValueError("Group not permitted")
        if any(sum(amounts, Fraction(0)) != tv[ti][1] for ti, amounts in allocations_by_tx.items()):
            raise ValueError("Incomplete allocation")
    except (ValueError, TypeError, KeyError):
        error("configuration", "Explicit allocation does not independently balance source transactions")

    usage, matched = defaultdict(list), defaultdict(list)
    statuses = {"MATCHED", "UNMATCHED", "AMBIGUOUS", "CONFLICT", "REVIEW_REQUIRED"}
    for row in rows:
        oi = row.get("source_index")
        if type(oi) is not int or not 0 <= oi < len(orders):
            continue
        tag = "order index " + str(oi)
        status, links, normalized, raw = (row.get(k) for k in ("status", "links", "normalized", "raw"))
        if (not isinstance(status, str) or status not in statuses or not isinstance(links, list) or not isinstance(normalized, dict)
                or not isinstance(raw, dict)):
            error("structure", tag + ": invalid row fields")
            continue
        if row.get("order_id") != orders[oi].get("id") or raw.get("order") != orders[oi]:
            error("source_trace", tag + ": source order differs")
        reasons = row.get("reasons")
        if not isinstance(reasons, list) or not reasons or not all(isinstance(r, str) and r for r in reasons):
            error("evidence", tag + ": reasons missing")
        if (status == "MATCHED" and row.get("uncertainty") is not None
                or status != "MATCHED" and not row.get("uncertainty")):
            error("evidence", tag + ": uncertainty inconsistent with status")
        candidates = row.get("transaction_source_indices")
        if (not isinstance(candidates, list) or not all(type(i) is int and 0 <= i < len(transactions) for i in candidates)
                or len(set(candidates)) != len(candidates)):
            error("source_trace", tag + ": invalid candidate coordinates")
        elif (raw.get("transactions") != [transactions[i] for i in candidates]
              or row.get("candidates") != [transactions[i].get("id") for i in candidates]):
            error("source_trace", tag + ": candidate source trace differs")
        if oi not in ov:
            if status != "CONFLICT" or links:
                error("invalid_source", tag + ": invalid order has a financial conclusion")
            continue
        identity, amount, currency, order_date = ov[oi]
        try:
            if (_number(normalized.get("order_amount")) != amount
                    or normalized.get("currency") != currency or normalized.get("date") != order_date.isoformat()):
                error("normalization", tag + ": normalized order differs from source")
        except ValueError:
            error("normalization", tag + ": invalid normalized money")
        if status not in {"MATCHED", "REVIEW_REQUIRED"} and links:
            error("conclusion", tag + ": unresolved status cannot consume transactions")
        if status == "MATCHED" and not links:
            error("conclusion", tag + ": confirmed match has no source payment")
        row_payments, seen = [], set()
        for link in links:
            try:
                if not isinstance(link, dict) or set(link) != {"transaction_id", "amount"}:
                    raise ValueError("Invalid link")
                ti = tx_ids[link["transaction_id"]]
                value = _number(link["amount"])
                if ti in seen:
                    error("double_count", tag + ": repeated transaction link")
                seen.add(ti)
                row_payments.append((ti, value))
                usage[ti].append((oi, value))
                if not isinstance(candidates, list) or ti not in candidates:
                    error("source_trace", tag + ": consumed payment is absent from candidate trace")
                if tv[ti][2] != currency:
                    error("currency", tag + ": source currencies differ")
                if abs((tv[ti][3] - order_date).days) > days:
                    error("date", tag + ": source date exceeds tolerance")
                if (amount < 0 or value < 0) and "refund" not in allowed:
                    error("permission", tag + ": refund not permitted")
            except (ValueError, TypeError, KeyError):
                error("links", tag + ": invalid or unknown payment/amount")
        if not row_payments:
            continue
        rule = row.get("rule")
        if rule == "group:explicit":
            if "group" not in allowed or any(explicit.get((oi, ti)) != value for ti, value in row_payments):
                error("identity", tag + ": group assignment is not explicitly authorized")
        else:
            if isinstance(rule, str) and rule.startswith("partial:"):
                rule = rule[len("partial:"):]
            if _first_rule(orders[oi], transactions, tv, rules) != rule:
                error("priority", tag + ": selected rule bypasses an earlier source cluster")
            kind, separator, key_text = rule.partition(":") if isinstance(rule, str) else ("", "", "")
            fields = key_text.split(",")
            key = _identity(orders[oi], fields)
            if (kind not in {"exact", "composite"} or kind not in allowed or not separator
                    or fields not in keys or (len(fields) == 1) != (kind == "exact") or key is None):
                error("identity", tag + ": no permitted confirmed key rule")
            elif sum(_identity(source, fields) == key for source in orders) != 1:
                error("identity", tag + ": confirmed key is not unique in the source")
            elif any(_identity(source, fields) == key for ti, source in enumerate(transactions) if ti in invalid_transactions):
                error("identity", tag + ": related source payment has unverified financial data")
            elif any(_identity(transactions[ti], fields) != key for ti, _ in row_payments):
                error("identity", tag + ": source keys do not match")
            for ti, value in row_payments:
                if value != tv[ti][1]:
                    error("amount", tag + ": implicit partial transaction allocation")
            if len(row_payments) > 1 and "partial" not in allowed:
                error("permission", tag + ": partial aggregation not permitted")
        total = sum((value for _, value in row_payments), Fraction(0))
        try:
            if _number(normalized.get("payment_total")) != total:
                error("amount", tag + ": payment_total differs from independent sum")
        except ValueError:
            error("amount", tag + ": payment_total is not exact money text")
        if status == "MATCHED":
            if abs(total - amount) > tolerance:
                error("amount", tag + ": MATCHED amount exceeds explicit tolerance")
            matched[currency].append(total)
        elif status == "REVIEW_REQUIRED":
            if "partial" not in allowed or not ((0 < total < amount) or (amount < total < 0)):
                error("conclusion", tag + ": unsupported partial-payment claim")

    for ti, uses in usage.items():
        if len(uses) > 1 and not all((oi, ti) in explicit and explicit[(oi, ti)] == value for oi, value in uses):
            error("double_count", "Transaction index " + str(ti) + " has unauthorized repeated use")
        if (ti in allocations_by_tx and any(explicit.get((oi, ti)) != value for oi, value in uses)):
            error("allocation", "Source transaction assigned outside its explicit allocation")
    expected_unresolved = {}
    for ti, raw_tx in enumerate(transactions):
        if ti in invalid_transactions:
            expected_unresolved[ti] = None
        else:
            left = tv[ti][1] - sum((value for _, value in usage[ti]), Fraction(0))
            if not usage[ti] or left:
                expected_unresolved[ti] = left
    unresolved_indexes = [item.get("source_index") for item in unresolved]
    if (not all(type(i) is int for i in unresolved_indexes)
            or sorted(unresolved_indexes) != sorted(expected_unresolved)):
        error("unresolved", "Unresolved transaction coverage differs from source residuals")
    for item in unresolved:
        ti = item.get("source_index")
        if type(ti) is not int or ti not in expected_unresolved:
            continue
        try:
            if item.get("record_id") != transactions[ti].get("id") or not item.get("reason"):
                raise ValueError("Missing identity or reason")
            if item.get("raw") != transactions[ti] or not isinstance(item.get("normalized"), dict):
                raise ValueError("Lost unresolved source values")
            expected = expected_unresolved[ti]
            if (expected is None and item.get("remaining_amount") is not None
                    or expected is not None and _number(item.get("remaining_amount")) != expected):
                raise ValueError("Wrong residual")
            normalized = item["normalized"]
            if expected is None:
                if normalized:
                    raise ValueError("Invalid source has asserted normalized values")
            elif (_number(normalized.get("amount")) != tv[ti][1]
                  or normalized.get("currency") != tv[ti][2] or normalized.get("date") != tv[ti][3].isoformat()):
                raise ValueError("Unresolved normalization differs from source")
        except (ValueError, TypeError):
            error("unresolved", "Unresolved amount/trace differs at transaction index " + str(ti))
    expected_exceptions = {("orders", i) for i in invalid_orders} | {("transactions", i) for i in invalid_transactions}
    actual_exceptions = []
    for item in exceptions:
        source, index = item.get("source"), item.get("source_index")
        if not isinstance(source, str) or source not in {"orders", "transactions"} or type(index) is not int:
            error("exceptions", "Invalid exception coordinates")
            continue
        actual_exceptions.append((source, index))
        raw_sources = orders if source == "orders" else transactions
        if (not 0 <= index < len(raw_sources) or item.get("record_id") != raw_sources[index].get("id")
                or not isinstance(item.get("reason"), str) or not item["reason"]):
            error("exceptions", "Exception lost its source identity/reason")
    if set(actual_exceptions) != expected_exceptions or len(actual_exceptions) != len(expected_exceptions):
        error("exceptions", "Invalid-source exception coverage is incomplete or duplicated")
    if (type(summary.get("orders")) is not int or summary.get("orders") != len(orders)
            or type(summary.get("transactions")) is not int or summary.get("transactions") != len(transactions)
            or summary.get("by_status") != dict(Counter(row.get("status") if isinstance(row.get("status"), str) else "<INVALID>" for row in rows))):
        error("summary", "Source/status counts differ")
    totals = summary.get("matched_totals")
    if not isinstance(totals, dict) or set(totals) != set(matched):
        error("summary", "Matched currency aggregate coverage differs")
    else:
        try:
            if any(_number(totals[c]) != sum(values, Fraction(0)) for c, values in matched.items()):
                error("summary", "Matched aggregates differ from independent exact sums")
        except ValueError:
            error("summary", "Matched aggregates are not decimal strings")
    checks.append({"check": "independent_source_invariants", "status": "FAIL" if any(e["check"] != "schema" for e in errors) else "PASS"})
    return finish()
