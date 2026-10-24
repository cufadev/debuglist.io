"""Reading and writing address lists (CSV with any delimiter, or one address per line)."""

import csv
import io
import re

from . import checks

_EMAIL_HEADERS = ("email", "e-mail", "mail", "correo", "correo electronico", "correo electrónico")
_EXTRA_COLUMNS = ["bugmail_status", "bugmail_reason", "bugmail_suggestion"]


def read_table(text: str, column: str = None):
    """Parse CSV/TXT content. Returns (header, rows, email_column_index)."""
    text = text.lstrip("﻿")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    if not rows:
        return ["email"], [], 0

    if any("@" in cell for cell in rows[0]):          # no header row
        header = [f"col{i + 1}" for i in range(len(rows[0]))]
        header[_first_with_at(rows[0])] = "email"
    else:
        header, rows = rows[0], rows[1:]

    lowered = [h.strip().lower() for h in header]
    if column:
        if column.strip().lower() not in lowered:
            raise ValueError(f"column '{column}' not found; available: {', '.join(header)}")
        return header, rows, lowered.index(column.strip().lower())
    for name in _EMAIL_HEADERS:
        if name in lowered:
            return header, rows, lowered.index(name)
    for row in rows:                                   # fall back to the first cell containing '@'
        if any("@" in c for c in row):
            return header, rows, _first_with_at(row)
    return header, rows, 0


def _first_with_at(row):
    return next((i for i, c in enumerate(row) if "@" in c), 0)


def extract_addresses(text: str):
    """Pull anything that looks like an address out of free text (pasted lists, one per line, etc.)."""
    return [t for t in re.split(r"[\s,;<>\"']+", text) if "@" in t]


def write_table(header, rows, idx, results, out):
    """Write the original columns plus the verdict. `results` maps normalized address -> Result."""
    writer = csv.writer(out)
    writer.writerow(list(header) + _EXTRA_COLUMNS)
    for row in rows:
        row = list(row) + [""] * (len(header) - len(row))
        res = results.get(checks.normalize(row[idx]))
        writer.writerow(row + ([res.status, res.reason, res.suggestion] if res else ["invalid", "empty", ""]))


def summarize(results):
    counts = {"valid": 0, "risky": 0, "unknown": 0, "invalid": 0}
    for res in results.values():
        counts[res.status] = counts.get(res.status, 0) + 1
    return counts
