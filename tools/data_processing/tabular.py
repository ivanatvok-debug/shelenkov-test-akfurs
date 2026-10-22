"""Literal tabular input. Encoding guesses that change text require a choice."""

import csv
import hashlib
import io
from itertools import islice
from pathlib import Path


class FormatUncertain(ValueError):
    """A caller must specify a format before processing can continue."""


def read_csv(path, encoding=None, delimiter=None) -> dict:
    raw = Path(path).read_bytes()
    if encoding is None:
        if raw.startswith(b"\xef\xbb\xbf"):
            encoding, basis = "utf-8-sig", "BOM"
        elif raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            encoding, basis = "utf-16", "BOM"
        else:
            encoding, basis = "utf-8", "validated UTF-8; not a probabilistic encoding claim"
    else:
        basis = "explicit"
    try:
        text = raw.decode(encoding, errors="strict")
    except (UnicodeError, LookupError) as exc:
        raise FormatUncertain("Encoding is not confirmed; supply an explicit encoding") from exc
    if "\x00" in text:
        raise FormatUncertain("Unexpected NUL characters; verify the encoding")
    if delimiter is None:
        possible, interpretations = [], []
        for candidate in ",;\t|":
            try:
                sample = list(islice(csv.reader(io.StringIO(text, newline=""), delimiter=candidate, strict=True), 101))
            except csv.Error:
                continue
            nonempty = [r for r in sample if r]
            interpretations.append(sample)
            widths = {len(r) for r in nonempty}
            if len(widths) == 1 and next(iter(widths)) > 1:
                possible.append(candidate)
        if len(possible) == 1:
            delimiter = possible[0]
        elif len(possible) == 0 and interpretations and all(v == interpretations[0] for v in interpretations):
            delimiter = ","  # Equivalent empty/single-column interpretations.
        else:
            raise FormatUncertain("Delimiter is uncertain; supply it explicitly")
    if not isinstance(delimiter, str) or len(delimiter) != 1 or delimiter in "\r\n\x00\"":
        raise ValueError("Delimiter must be one non-quote, non-newline character")
    parsed, row_numbers = [], []
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
    try:
        start = 1
        for row in reader:
            parsed.append(row)
            row_numbers.append(start)
            start = reader.line_num + 1
    except csv.Error as exc:
        raise ValueError("Invalid CSV structure") from exc
    return {"format": "csv", "encoding": encoding, "encoding_basis": basis,
            "delimiter": delimiter, "headers": parsed[0] if parsed else [],
            "rows": parsed[1:], "source_row_numbers": row_numbers[1:],
            "source_sha256": hashlib.sha256(raw).hexdigest()}
