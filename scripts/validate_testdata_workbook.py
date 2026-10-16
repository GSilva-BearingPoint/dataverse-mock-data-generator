from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from testdata_common import INTERNAL_KEY


def load_metadata(workbook) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    metadata = workbook["_Metadata"]
    run: dict[str, Any] = {}
    tables: dict[str, dict[str, Any]] = {}
    chunks: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for kind, name, part, raw_json in metadata.iter_rows(min_row=2, values_only=True):
        chunks.setdefault((kind, name), []).append((int(part), raw_json))
    for (kind, name), parts in chunks.items():
        raw_json = "".join(value for _part, value in sorted(parts))
        value = json.loads(raw_json)
        if kind == "run":
            run = value
        elif kind == "table":
            tables[name] = value
    return run, tables


def read_rows(workbook, table: dict[str, Any]) -> list[dict[str, Any]]:
    worksheet = workbook[table["sheet_name"]]
    headers = [cell.value for cell in worksheet[1]]
    rows = []
    for values in worksheet.iter_rows(min_row=2, values_only=True):
        if not any(value is not None and value != "" for value in values):
            continue
        rows.append(dict(zip(headers, values)))
    return rows


def validate(path: Path) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    workbook = load_workbook(path, data_only=False)
    _run, tables = load_metadata(workbook)
    rows_by_table = {name: read_rows(workbook, table) for name, table in tables.items()}
    errors: list[str] = []

    keys_by_table: dict[str, set[str]] = {}
    for name, rows in rows_by_table.items():
        keys = [row.get(INTERNAL_KEY) for row in rows]
        missing = [index + 2 for index, key in enumerate(keys) if not key]
        duplicates = sorted({key for key in keys if key and keys.count(key) > 1})
        if missing:
            errors.append(f"{name}: missing {INTERNAL_KEY} on Excel rows {missing}.")
        if duplicates:
            errors.append(f"{name}: duplicate {INTERNAL_KEY} values: {duplicates}.")
        keys_by_table[name] = {str(key) for key in keys if key}

        table = tables[name]
        required = [
            column["logical_name"]
            for column in table.get("columns", [])
            if column.get("required")
        ]
        for row_number, row in enumerate(rows, start=2):
            for column in required:
                if row.get(column) is None or row.get(column) == "":
                    errors.append(f"{name}!{row_number}: required column {column} is blank.")

    for name, rows in rows_by_table.items():
        table = tables[name]
        for lookup in table.get("lookups", []):
            target_keys = keys_by_table.get(lookup["target_table"], set())
            for row_number, row in enumerate(rows, start=2):
                value = row.get(lookup["column"])
                if not value:
                    if lookup.get("required"):
                        errors.append(
                            f"{name}!{row_number}: required lookup {lookup['column']} is blank."
                        )
                    continue
                if str(value) not in target_keys:
                    errors.append(
                        f"{name}!{row_number}: lookup {lookup['column']} points to "
                        f"unknown {lookup['target_table']} key {value!r}."
                    )
    return rows_by_table, errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate an edited test-data workbook offline.")
    parser.add_argument("--workbook", required=True)
    args = parser.parse_args()
    rows_by_table, errors = validate(Path(args.workbook))
    if errors:
        print("Workbook is invalid:")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)
    counts = ", ".join(f"{name}={len(rows)}" for name, rows in rows_by_table.items())
    print(f"Workbook is valid: {counts}")


if __name__ == "__main__":
    main()
