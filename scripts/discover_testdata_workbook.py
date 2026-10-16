from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from build_testdata_workbook import build_workbook
from testdata_common import SIZE_COUNTS, default_workbook_path, validate_seed_email


DELEGATED_AUTH_BLOCKERS = (
    "CLIENT_ID",
    "CLIENT_SECRET",
    "CLIENT_CERTIFICATE_PATH",
    "CLIENT_CERTIFICATE_PASSWORD",
)


def require_delegated_auth() -> None:
    configured = [name for name in DELEGATED_AUTH_BLOCKERS if os.environ.get(name)]
    if configured:
        raise RuntimeError(
            "This workflow only accepts the user-delegated authentication configured by "
            f"the Microsoft dv-connect skill. Remove: {', '.join(configured)}."
        )


SYSTEM_LOOKUPS = {
    "createdby",
    "createdonbehalfby",
    "modifiedby",
    "modifiedonbehalfby",
    "ownerid",
    "owningbusinessunit",
    "owningteam",
    "owninguser",
    "transactioncurrencyid",
}
SKIP_TYPES = {
    "EntityName",
    "ManagedProperty",
    "Owner",
    "State",
    "Status",
    "Uniqueidentifier",
    "Virtual",
}
SUPPORTED_TYPES = {
    "BigInt",
    "Boolean",
    "DateTime",
    "Decimal",
    "Double",
    "Integer",
    "Memo",
    "Money",
    "Picklist",
    "String",
}


def import_get_client():
    candidates = [
        Path(os.environ["DV_TESTDATA_AUTH_DIR"]) if os.environ.get("DV_TESTDATA_AUTH_DIR") else None,
        Path.cwd() / "scripts",
        Path.home()
        / ".copilot"
        / "installed-plugins"
        / "awesome-copilot"
        / "dataverse"
        / "scripts",
    ]
    for candidate in candidates:
        if candidate and (candidate / "auth.py").exists():
            sys.path.insert(0, str(candidate))
            return importlib.import_module("auth").get_client
    raise RuntimeError(
        "auth.py was not found. Set DV_TESTDATA_AUTH_DIR to the Dataverse plugin scripts folder."
    )


def localized_label(value: Any, fallback: str) -> str:
    if not isinstance(value, dict):
        return fallback
    user = value.get("UserLocalizedLabel")
    if isinstance(user, dict) and user.get("Label"):
        return str(user["Label"])
    labels = value.get("LocalizedLabels") or []
    if labels and isinstance(labels[0], dict) and labels[0].get("Label"):
        return str(labels[0]["Label"])
    return fallback


def required_level(column: dict[str, Any]) -> str:
    value = column.get("RequiredLevel")
    if isinstance(value, dict):
        return str(value.get("Value") or "None")
    return str(value or "None")


def option_values(column: dict[str, Any]) -> list[dict[str, Any]]:
    option_set = column.get("OptionSet") or column.get("GlobalOptionSet") or {}
    result = []
    for option in option_set.get("Options") or []:
        if option.get("Value") is None:
            continue
        result.append(
            {
                "value": option["Value"],
                "label": localized_label(option.get("Label"), str(option["Value"])),
            }
        )
    return result


def alternate_key_columns(client, table_name: str) -> set[str]:
    result: set[str] = set()
    try:
        keys = client.tables.get_alternate_keys(table_name)
    except (AttributeError, NotImplementedError):
        return result
    for key in keys or []:
        result.update(key.get("KeyAttributes") or key.get("key_attributes") or [])
    return result


def relationship_candidates(client, table_name: str) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for relationship in client.tables.list_table_relationships(table_name) or []:
        referencing_entity = relationship.get("ReferencingEntity")
        attribute = relationship.get("ReferencingAttribute")
        target = relationship.get("ReferencedEntity")
        navigation = relationship.get("ReferencingEntityNavigationPropertyName")
        if (
            referencing_entity == table_name
            and attribute
            and target
            and navigation
            and attribute not in SYSTEM_LOOKUPS
        ):
            grouped[attribute].append(
                {
                    "column": attribute,
                    "navigation_property": navigation,
                    "target_table": target,
                }
            )
    return grouped


def inspect_table(
    client,
    table_name: str,
    count: int,
    all_writable: bool,
    context_rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context_rules = context_rules or {}
    context_columns = set((context_rules.get("defaults") or {}).keys())
    context_columns.update((context_rules.get("allowed_values") or {}).keys())
    context_columns.update((context_rules.get("templates") or {}).keys())
    for calculation in context_rules.get("calculations") or []:
        context_columns.add(calculation["target"])
        context_columns.update(calculation.get("sources") or [])
    table = client.tables.get(table_name)
    if not table:
        raise ValueError(f"Dataverse table not found: {table_name}")
    primary_name = table.get("PrimaryNameAttribute") or table.get("primary_name_attribute")
    alternate_keys = alternate_key_columns(client, table_name)
    relationships = relationship_candidates(client, table_name)
    raw_columns = client.tables.list_columns(
        table_name,
        typed=True,
    )

    columns = []
    lookup_columns: dict[str, dict[str, Any]] = {}
    for raw in raw_columns:
        logical_name = raw.get("LogicalName")
        attr_type = raw.get("AttributeType")
        if not logical_name or raw.get("AttributeOf") is not None:
            continue
        if raw.get("IsPrimaryId") or raw.get("IsValidForCreate") is False:
            continue
        level = required_level(raw)
        required = level == "ApplicationRequired"
        recommended = level == "Recommended"
        required_level_metadata = raw.get("RequiredLevel") or {}
        if (
            isinstance(required_level_metadata, dict)
            and required_level_metadata.get("CanBeChanged") is False
        ):
            continue
        if attr_type in {"Lookup", "Customer"}:
            if logical_name not in SYSTEM_LOOKUPS:
                lookup_columns[logical_name] = {
                    "required": required,
                    "recommended": recommended,
                    "candidates": relationships.get(logical_name, []),
                }
            continue
        if attr_type in SKIP_TYPES or attr_type not in SUPPORTED_TYPES:
            continue

        fmt = raw.get("Format")
        is_email = attr_type == "String" and fmt == "Email"
        include = all_writable or required or recommended or is_email or logical_name in context_columns
        if not include:
            continue
        column = {
            "logical_name": logical_name,
            "label": localized_label(raw.get("DisplayName"), logical_name),
            "type": attr_type,
            "required": required,
            "recommended": recommended,
            "unique": logical_name in alternate_keys,
        }
        if fmt:
            column["format"] = fmt
        for source, target in (
            ("MinValue", "min_value"),
            ("MaxValue", "max_value"),
            ("MaxLength", "max_length"),
        ):
            if raw.get(source) is not None:
                column[target] = raw[source]
        if attr_type == "Picklist":
            column["options"] = option_values(raw)
        columns.append(column)

    unsupported_context_columns = context_columns.difference(
        column["logical_name"] for column in columns
    )
    if unsupported_context_columns:
        raise ValueError(
            f"{table_name}: repository context references missing or non-writable columns: "
            + ", ".join(sorted(unsupported_context_columns))
        )

    return {
        "logical_name": (
            table.get("LogicalName")
            or table.get("table_logical_name")
            or table_name
        ),
        "display_name": localized_label(table.get("DisplayName"), table_name),
        "entity_set_name": table.get("EntitySetName") or table.get("entity_set_name"),
        "primary_id_column": (
            table.get("PrimaryIdAttribute")
            or table.get("primary_id_attribute")
        ),
        "primary_name_column": primary_name,
        "count": count,
        "columns": columns,
        "context_rules": context_rules,
        "_lookup_columns": lookup_columns,
    }


def discover_plan(
    client,
    table_names: list[str],
    count: int,
    seed_email: str | None,
    environment: str,
    all_writable: bool = False,
    include_required_related: bool = True,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = context or {}
    context_tables = context.get("tables") or {}
    requested = list(dict.fromkeys(table_names))
    queue = deque(requested)
    inspected: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []

    while queue:
        table_name = queue.popleft()
        if table_name in inspected:
            continue
        info = inspect_table(
            client,
            table_name,
            count,
            all_writable,
            context_tables.get(table_name),
        )
        inspected[table_name] = info
        if not include_required_related:
            continue
        for column_name, lookup in info["_lookup_columns"].items():
            if not lookup["required"]:
                continue
            targets = {
                candidate["target_table"]
                for candidate in lookup["candidates"]
                if candidate["target_table"] != table_name
            }
            if len(targets) == 1:
                target = next(iter(targets))
                if target not in inspected:
                    queue.append(target)
            elif len(targets) > 1:
                warnings.append(
                    f"{table_name}.{column_name} is a required polymorphic lookup; "
                    f"choose one target: {', '.join(sorted(targets))}."
                )
            else:
                warnings.append(
                    f"{table_name}.{column_name} is required but its target relationship "
                    "could not be resolved."
                )

    selected_names = set(inspected)
    tables = []
    for table_name, info in inspected.items():
        lookups = []
        for column_name, lookup in info.pop("_lookup_columns").items():
            candidates = [
                candidate
                for candidate in lookup["candidates"]
                if candidate["target_table"] in selected_names
                and candidate["target_table"] != table_name
            ]
            if not candidates:
                if lookup["required"]:
                    possible = sorted(
                        {
                            candidate["target_table"]
                            for candidate in lookup["candidates"]
                            if candidate["target_table"] != table_name
                        }
                    )
                    raise ValueError(
                        f"{table_name}.{column_name} is a required lookup but no target table "
                        f"is included. Add one of: {', '.join(possible) or 'unknown target'}."
                    )
                continue
            if len(candidates) > 1:
                raise ValueError(
                    f"{table_name}.{column_name} can target multiple included tables: "
                    f"{', '.join(sorted(item['target_table'] for item in candidates))}. "
                    "Generate separate plans or include only the intended target table."
                )
            chosen = sorted(candidates, key=lambda item: item["target_table"])[0]
            lookups.append(
                {
                    **chosen,
                    "required": lookup["required"],
                    "recommended": lookup["recommended"],
                }
            )
        info["lookups"] = lookups
        if not info.get("entity_set_name"):
            raise ValueError(f"{table_name}: EntitySetName was not returned by Dataverse.")
        tables.append(info)

    email_columns = [
        f"{table['logical_name']}.{column['logical_name']}"
        for table in tables
        for column in table["columns"]
        if column.get("type") == "String" and column.get("format") == "Email"
    ]
    if email_columns and not seed_email:
        raise ValueError(
            "A seed email is required because these columns need email values: "
            + ", ".join(email_columns)
        )

    return {
        "name": "dataverse-testdata",
        "environment": environment,
        "seed_email": seed_email,
        "context_source": context.get("source"),
        "context_notes": context.get("notes") or [],
        "warnings": sorted(set(warnings)),
        "tables": tables,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect a live Dataverse environment and generate the Excel approval workbook "
            "from its current table metadata and relationships."
        )
    )
    parser.add_argument("--tables", required=True, help="Comma-separated table logical names.")
    volume = parser.add_mutually_exclusive_group(required=True)
    volume.add_argument("--size", choices=sorted(SIZE_COUNTS))
    volume.add_argument("--count", type=int)
    parser.add_argument(
        "--environment-url",
        required=True,
        help="Environment explicitly selected through the Microsoft dv-connect skill.",
    )
    parser.add_argument("--seed-email", required=True)
    parser.add_argument(
        "--output",
        help="Excel workbook returned for approval. Defaults to the user's Desktop.",
    )
    parser.add_argument("--plan-output", help="Optional JSON metadata snapshot for audit/debugging.")
    parser.add_argument(
        "--context-rules",
        help="Optional JSON rules extracted from a user-confirmed plugin/custom-process repository.",
    )
    parser.add_argument("--all-writable-columns", action="store_true")
    parser.add_argument("--no-required-related", action="store_true")
    args = parser.parse_args()

    count = args.count if args.count is not None else SIZE_COUNTS[args.size]
    if count <= 0:
        raise ValueError("Record count must be positive.")
    os.environ["DATAVERSE_URL"] = args.environment_url.rstrip("/")
    get_client = import_get_client()
    require_delegated_auth()
    client = get_client("dv-data")
    # A real read proves the selected environment is reachable with the signed-in user's account.
    organizations = client.records.list(
        "organization",
        select=["organizationid"],
        top=1,
    )
    if not organizations:
        raise RuntimeError("Authenticated connection is valid but no organization row was returned.")
    table_names = [value.strip() for value in args.tables.split(",") if value.strip()]
    context = {}
    if args.context_rules:
        with Path(args.context_rules).open(encoding="utf-8") as handle:
            context = json.load(handle)
    plan = discover_plan(
        client=client,
        table_names=table_names,
        count=count,
        seed_email=validate_seed_email(args.seed_email),
        environment=args.environment_url.rstrip("/"),
        all_writable=args.all_writable_columns,
        include_required_related=not args.no_required_related,
        context=context,
    )
    if args.plan_output:
        plan_path = Path(args.plan_output)
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        with plan_path.open("w", encoding="utf-8") as handle:
            json.dump(plan, handle, indent=2)
    output_path = (
        Path(args.output)
        if args.output
        else default_workbook_path(table_names, count)
    )
    build_workbook(plan, output_path)
    print(f"Approval workbook written: {output_path.resolve()}")


if __name__ == "__main__":
    main()
