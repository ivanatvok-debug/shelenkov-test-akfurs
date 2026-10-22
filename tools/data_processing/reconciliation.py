"""Deterministic matching of canonical records under explicitly supplied rules.

No file access, network, corpus-specific rules, or financial float conversion.
Exact keys establish identity; amounts and dates only verify that identity.
"""

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from difflib import SequenceMatcher
import re
import unicodedata


_MONEY = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?\Z")
_TYPES = {"exact", "composite", "normalized", "fuzzy", "partial", "group", "refund"}
_SEARCH = {"exact", "composite", "normalized", "fuzzy"}


def _money(value):
    if not isinstance(value, str) or not _MONEY.fullmatch(value):
        raise ValueError("Money must be an unambiguous decimal string without exponent")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Invalid decimal money") from exc
    if not result.is_finite():
        raise ValueError("Money must be finite")
    return result


def _sum(values):
    values = list(values)
    if not values:
        return Decimal("0")
    integer_digits = max(max(v.adjusted() + 1, 1) for v in values)
    fraction_digits = max(max(-v.as_tuple().exponent, 0) for v in values)
    with localcontext() as context:
        context.prec = integer_digits + fraction_digits + len(str(len(values))) + 2
        return sum(values, Decimal("0"))


def _key(record, fields):
    parts = tuple(record.get(field) for field in fields)
    return parts if all(isinstance(p, str) and p.strip() for p in parts) else None


def _text(value):
    if not isinstance(value, str):
        return ""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _config(rules):
    if not isinstance(rules, dict):
        raise ValueError("Rules must be an object")
    required = {"keys", "currencies", "amount_tolerance", "date_tolerance_days",
                "allowed_types", "rule_order"}
    if not required <= rules.keys():
        raise ValueError("Rules require explicit keys, currencies, tolerances, types and order")
    allowed = rules["allowed_types"]
    order = rules["rule_order"]
    if (not isinstance(allowed, list) or not all(isinstance(v, str) for v in allowed)
            or not set(allowed) <= _TYPES or len(set(allowed)) != len(allowed)):
        raise ValueError("Unsupported or duplicated allowed match type")
    if (not isinstance(order, list) or not all(isinstance(v, str) for v in order)
            or set(order) != set(allowed) & _SEARCH or len(set(order)) != len(order)):
        raise ValueError("rule_order must list each permitted search type exactly once")
    currencies = rules["currencies"]
    if (not isinstance(currencies, list) or not currencies
            or not all(isinstance(v, str) and v and v == v.strip() for v in currencies)
            or len(set(currencies)) != len(currencies)):
        raise ValueError("Currencies must be explicit unique strings")
    tolerance = _money(rules["amount_tolerance"])
    days = rules["date_tolerance_days"]
    if tolerance < 0 or type(days) is not int or days < 0:
        raise ValueError("Tolerances must be nonnegative; days must be an integer")
    keys = rules["keys"]
    if (not isinstance(keys, list)
            or not all(isinstance(k, list) and k and
                       all(isinstance(f, str) and f for f in k) and
                       len(set(k)) == len(k) for k in keys)):
        raise ValueError("keys must be lists of distinct field names")
    if len({tuple(k) for k in keys}) != len(keys):
        raise ValueError("Duplicate key rule")
    for kind in (set(order) & {"exact", "composite"}):
        if not any((len(k) == 1) == (kind == "exact") for k in keys):
            raise ValueError("Missing key fields for permitted search type")
    if set(order) & {"normalized", "fuzzy"}:
        if not isinstance(rules.get("candidate_field"), str) or not rules["candidate_field"]:
            raise ValueError("String search requires candidate_field")
    if "fuzzy" in order:
        threshold = _money(rules.get("fuzzy_threshold"))
        if not 0 < threshold <= 1:
            raise ValueError("fuzzy_threshold must be a decimal string in (0, 1]")
    allocations = rules.get("allocations", [])
    if not isinstance(allocations, list) or (allocations and "group" not in allowed):
        raise ValueError("Explicit allocations require group permission")
    return tolerance


def _sources(records, source, currencies):
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise ValueError("Source must be a list of record objects")
    counts = Counter(r.get("id") for r in records if isinstance(r.get("id"), str))
    valid, exceptions = {}, []
    for index, raw in enumerate(records):
        try:
            identifier = raw.get("id")
            if not isinstance(identifier, str) or not identifier.strip():
                raise ValueError("MISSING_STRING_ID")
            if counts[identifier] != 1:
                raise ValueError("DUPLICATE_ID")
            amount = _money(raw.get("amount"))
            currency = raw.get("currency")
            if not isinstance(currency, str) or currency not in currencies:
                raise ValueError("UNKNOWN_CURRENCY")
            raw_date = raw.get("date")
            if not isinstance(raw_date, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw_date):
                raise ValueError("DATE_REQUIRES_ISO_YYYY_MM_DD")
            parsed_date = date.fromisoformat(raw_date)
            valid[index] = {"id": identifier, "amount": amount,
                            "currency": currency, "date": parsed_date}
        except ValueError as exc:
            exceptions.append({"source": source, "source_index": index,
                               "record_id": raw.get("id"), "reason": str(exc)})
    return valid, exceptions


def _allocation_map(orders, transactions, order_values, transaction_values, rules):
    by_order, by_transaction = defaultdict(list), defaultdict(list)
    order_ids = {v["id"]: i for i, v in order_values.items()}
    transaction_ids = {v["id"]: i for i, v in transaction_values.items()}
    pairs = set()
    for item in rules.get("allocations", []):
        if not isinstance(item, dict) or set(item) != {"order_id", "transaction_id", "amount"}:
            raise ValueError("Allocation requires order_id, transaction_id and decimal amount")
        if (not isinstance(item["order_id"], str) or not isinstance(item["transaction_id"], str)
                or item["order_id"] not in order_ids or item["transaction_id"] not in transaction_ids):
            raise ValueError("Allocation refers to an unknown or invalid source record")
        oi, ti = order_ids[item["order_id"]], transaction_ids[item["transaction_id"]]
        amount = _money(item["amount"])
        source_amount = transaction_values[ti]["amount"]
        if (oi, ti) in pairs or amount == 0 or (amount > 0) != (source_amount > 0):
            raise ValueError("Duplicate, zero or wrong-sign allocation")
        if order_values[oi]["currency"] != transaction_values[ti]["currency"]:
            raise ValueError("Allocation currencies differ")
        pairs.add((oi, ti))
        by_order[oi].append((ti, amount))
        by_transaction[ti].append(amount)
    for ti, amounts in by_transaction.items():
        if _sum(amounts) != transaction_values[ti]["amount"]:
            raise ValueError("Allocations must account for the full source transaction amount")
    return by_order, pairs


def _decision(order, payments, tolerance, days, allowed):
    if any(v["currency"] != order["currency"] for v, _ in payments):
        return "CONFLICT", ["CURRENCY_MISMATCH"]
    if any(abs((v["date"] - order["date"]).days) > days for v, _ in payments):
        return "CONFLICT", ["DATE_OUTSIDE_TOLERANCE"]
    if (order["amount"] < 0 or any(a < 0 for _, a in payments)) and "refund" not in allowed:
        return "REVIEW_REQUIRED", ["REFUND_NOT_AUTHORIZED"]
    total = _sum(amount for _, amount in payments)
    difference = _sum([total, order["amount"].copy_negate()]).copy_abs()
    if difference <= tolerance:
        return "MATCHED", ["CONFIRMED_IDENTITY_AMOUNT_CURRENCY_DATE"]
    partial = (total > 0 and order["amount"] > total) or (total < 0 and order["amount"] < total)
    if partial and "partial" in allowed:
        return "REVIEW_REQUIRED", ["PARTIAL_PAYMENT"]
    return "CONFLICT", ["AMOUNT_MISMATCH"]


def reconcile(orders: list[dict], transactions: list[dict], rules: dict) -> dict:
    """Match canonical records; raise ValueError for an invalid rule configuration.

    Every order remains in results, including invalid orders. Invalid transactions
    remain in exceptions and unresolved_transactions. Suggestions never create links.
    """
    tolerance = _config(rules)
    ov, oe = _sources(orders, "orders", rules["currencies"])
    tv, te = _sources(transactions, "transactions", rules["currencies"])
    allocations, allocated_pairs = _allocation_map(orders, transactions, ov, tv, rules)
    allocated_transactions = {ti for _, ti in allocated_pairs}
    exceptions = oe + te
    rows = []
    for oi, raw_order in enumerate(orders):
        row = {"source_index": oi, "order_id": raw_order.get("id"),
               "status": "UNMATCHED", "rule": None, "links": [], "candidates": [],
               "transaction_source_indices": [],
               "raw": {"order": deepcopy(raw_order), "transactions": []},
               "normalized": {}, "reasons": ["NO_CONFIRMED_IDENTITY"],
               "uncertainty": "NO_CONFIRMED_IDENTITY"}
        rows.append(row)
        if oi not in ov:
            row.update(status="CONFLICT", reasons=["INVALID_SOURCE"], uncertainty="INVALID_SOURCE")
            continue
        order = ov[oi]
        row["normalized"] = {"order_amount": format(order["amount"], "f"),
                             "currency": order["currency"], "date": order["date"].isoformat(),
                             "payment_total": "0"}
        proposed, candidates, selected_rule = [], [], None
        if oi in allocations:
            proposed = allocations[oi]
            candidates = [ti for ti, _ in proposed]
            selected_rule = "group:explicit"
        else:
            for kind in rules["rule_order"]:
                if kind in {"exact", "composite"}:
                    for fields in rules["keys"]:
                        if (len(fields) == 1) != (kind == "exact"):
                            continue
                        identity = _key(raw_order, fields)
                        if identity is None:
                            continue
                        found = [ti for ti, source in enumerate(transactions) if _key(source, fields) == identity]
                        if not found:
                            continue
                        candidates, selected_rule = found, kind + ":" + ",".join(fields)
                        same_orders = [i for i, source in enumerate(orders) if _key(source, fields) == identity]
                        if len(same_orders) != 1:
                            row.update(status="AMBIGUOUS", reasons=["KEY_NOT_UNIQUE"])
                        elif any(ti not in tv for ti in found):
                            row.update(status="CONFLICT", reasons=["INVALID_RELATED_SOURCE"])
                        elif len(found) > 1 and "partial" not in rules["allowed_types"]:
                            row.update(status="AMBIGUOUS", reasons=["MULTIPLE_TRANSACTIONS"])
                        else:
                            proposed = [(ti, tv[ti]["amount"]) for ti in found]
                        break
                else:
                    field = rules["candidate_field"]
                    value = _text(raw_order.get(field))
                    if value:
                        for ti in tv:
                            other = _text(transactions[ti].get(field))
                            if not other:
                                continue
                            similar = value == other if kind == "normalized" else (
                                SequenceMatcher(None, value, other, autojunk=False).ratio()
                                >= float(rules["fuzzy_threshold"]))
                            if similar:
                                candidates.append(ti)
                    if candidates:
                        selected_rule = kind + ":" + field
                        row["normalized"]["candidate_value"] = value
                        row.update(status="REVIEW_REQUIRED", reasons=["STRING_CANDIDATE_ONLY"])
                if selected_rule:
                    break
        if selected_rule is None:
            continue
        row["rule"] = selected_rule
        row["candidates"] = [transactions[ti].get("id") for ti in candidates]
        row["transaction_source_indices"] = candidates
        row["raw"]["transactions"] = [deepcopy(transactions[ti]) for ti in candidates]
        if proposed:
            payments = [(tv[ti], amount) for ti, amount in proposed]
            status, reasons = _decision(order, payments, tolerance, rules["date_tolerance_days"], rules["allowed_types"])
            row.update(status=status, reasons=reasons)
            row["normalized"]["payment_total"] = format(_sum(a for _, a in proposed), "f")
            if status == "MATCHED" or reasons == ["PARTIAL_PAYMENT"]:
                row["links"] = [{"transaction_id": tv[ti]["id"], "amount": format(a, "f")}
                                for ti, a in proposed]
            if len(proposed) > 1 and selected_rule != "group:explicit":
                row["rule"] = "partial:" + selected_rule
        row["uncertainty"] = None if row["status"] == "MATCHED" else ";".join(row["reasons"])

    # Resolve conflicts after all proposals: input ordering must not pick a winner.
    claims = defaultdict(list)
    transaction_ids = {v["id"]: ti for ti, v in tv.items()}
    for oi, row in enumerate(rows):
        for link in row["links"]:
            claims[link["transaction_id"]].append(oi)
    conflicts = set()
    for identifier, owners in claims.items():
        ti = transaction_ids[identifier]
        if len(owners) > 1 and not all((oi, ti) in allocated_pairs for oi in owners):
            conflicts.update(owners)
        if ti in allocated_transactions and any((oi, ti) not in allocated_pairs for oi in owners):
            conflicts.update(owners)
    for oi in conflicts:
        rows[oi].update(status="CONFLICT", links=[], reasons=["TRANSACTION_REUSE"],
                        uncertainty="TRANSACTION_REUSE")

    unresolved, matched = [], defaultdict(list)
    for ti, raw in enumerate(transactions):
        used = [_money(link["amount"]) for row in rows for link in row["links"]
                if link["transaction_id"] == raw.get("id")]
        if ti not in tv:
            remaining, reason = None, "INVALID_SOURCE"
        else:
            remaining = _sum([tv[ti]["amount"], *[v.copy_negate() for v in used]])
            reason = "UNALLOCATED_AMOUNT"
            if remaining == 0 and used:
                continue
        unresolved.append({"source_index": ti, "record_id": raw.get("id"), "reason": reason,
                           "remaining_amount": None if remaining is None else format(remaining, "f"),
                           "raw": deepcopy(raw),
                           "normalized": {} if ti not in tv else {
                               "amount": format(tv[ti]["amount"], "f"), "currency": tv[ti]["currency"],
                               "date": tv[ti]["date"].isoformat()}})
    for row in rows:
        if row["status"] == "MATCHED":
            matched[row["normalized"]["currency"]].append(_money(row["normalized"]["payment_total"]))
    return {"version": "1.0", "results": rows, "exceptions": exceptions,
            "unresolved_transactions": unresolved,
            "summary": {"orders": len(orders), "transactions": len(transactions),
                        "by_status": dict(sorted(Counter(r["status"] for r in rows).items())),
                        "matched_totals": {c: format(_sum(v), "f") for c, v in sorted(matched.items())}}}
