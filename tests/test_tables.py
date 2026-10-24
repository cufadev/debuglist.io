import io

import pytest

from bugmail.tables import read_table, write_table, extract_addresses, summarize
from bugmail.verifier import Result


def test_header_autodetected_with_semicolons():
    header, rows, idx = read_table("Nombre;Correo;Ciudad\nAna;ana@example.com;Salta\nLuis;luis@example.com;Jujuy\n")
    assert header == ["Nombre", "Correo", "Ciudad"] and idx == 1 and len(rows) == 2


def test_plain_list_without_header():
    header, rows, idx = read_table("ana@example.com\nluis@example.com\n")
    assert header == ["email"] and idx == 0 and len(rows) == 2


def test_explicit_column_and_error():
    text = "id,contacto\n1,ana@example.com\n"
    assert read_table(text, column="contacto")[2] == 1
    with pytest.raises(ValueError):
        read_table(text, column="nope")


def test_bom_and_blank_lines():
    header, rows, idx = read_table("﻿email\n\nana@example.com\n\n")
    assert rows == [["ana@example.com"]]


def test_extract_addresses_from_free_text():
    text = "Ana <ana@example.com>, luis@example.com; basura\n\"maria@example.org\""
    assert extract_addresses(text) == ["ana@example.com", "luis@example.com", "maria@example.org"]


def test_write_table_adds_verdict_columns():
    header, rows, idx = read_table("nombre,email\nAna,Ana@Example.com\nSin,\n")
    results = {"ana@example.com": Result("ana@example.com", "valid", "accepted")}
    out = io.StringIO()
    write_table(header, rows, idx, results, out)
    lines = out.getvalue().strip().splitlines()
    assert lines[0] == "nombre,email,bugmail_status,bugmail_reason,bugmail_suggestion"
    assert lines[1].startswith("Ana,Ana@Example.com,valid,accepted")
    assert lines[2].startswith("Sin,,invalid,empty")


def test_summarize():
    res = {"a": Result("a", "valid", ""), "b": Result("b", "invalid", ""), "c": Result("c", "valid", "")}
    assert summarize(res) == {"valid": 2, "risky": 0, "unknown": 0, "invalid": 1}
