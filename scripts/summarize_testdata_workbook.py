from __future__ import annotations

import argparse
from pathlib import Path

from openpyxl import load_workbook

from validate_testdata_workbook import load_metadata, read_rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print the short summary shown immediately before approval."
    )
    parser.add_argument("--workbook", required=True)
    args = parser.parse_args()

    workbook_path = Path(args.workbook)
    workbook = load_workbook(workbook_path, data_only=False)
    run, tables = load_metadata(workbook)
    total = 0
    table_summaries = []
    relationships = []
    for table_name in run["order"]:
        table = tables[table_name]
        rows = read_rows(workbook, table)
        total += len(rows)
        table_summaries.append(f"{len(rows)} {table_name}")
        for lookup in table.get("lookups", []):
            relationships.append(
                f"{table_name}.{lookup['column']} -> {lookup['target_table']}"
            )

    print(f"Environment: {run.get('environment')}")
    print(f"Create: {', '.join(table_summaries)} ({total} total records)")
    print(
        "Relationships: "
        + (", ".join(relationships) if relationships else "none in this workbook")
    )
    print(f"Repository context: {run.get('context_source') or 'none provided'}")
    print("Email aliases: compact +tags derived from the confirmed seed email")
    print(f"Workbook: {workbook_path.resolve()}")
    print("No Dataverse records have been written yet.")


if __name__ == "__main__":
    main()
