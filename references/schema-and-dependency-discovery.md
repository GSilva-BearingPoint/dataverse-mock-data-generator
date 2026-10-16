# Schema and Dependency Discovery

Run once per requested table before generating any data.

## 1. Required columns and attribute types

Reuse dv-data's sample-data-generation query (filtered to avoid duplicated lookup sub-attributes):

```python
attrs = client.tables.list_columns(
    TABLE,
    select=["LogicalName", "AttributeType", "RequiredLevel", "DisplayName"],
    filter="AttributeOf eq null",
)
required = [a for a in attrs if a["RequiredLevel"]["Value"] == "ApplicationRequired"]
```

Also capture **recommended-but-not-required** columns (`RequiredLevel.Value == "Recommended"`) — include
them in generation too, since business rules often treat them as effectively required even when metadata
doesn't mark them `ApplicationRequired`. Surface this distinction in the dry-run preview so the user can
drop them if unwanted.

## 2. Valid Picklist / Status option values

Never use a placeholder integer. For each Picklist/Status column, look up the real options:

```python
options = client.tables.describe(TABLE, include="all")  # or targeted OptionSet query
# Resolve the specific attribute's OptionSet values; pick randomly from valid Value entries only.
```

If an option set has a "Default/Inactive" value that shouldn't be used for new test records (e.g., a
"Closed" status), prefer values flagged as open/active where that distinction exists in the metadata
(`State` transitions). When ambiguous, default to the first non-deprecated option and note the choice in
the preview.

## 3. Alternate keys and natural uniqueness

```python
dataverse data describe --table <table> --include all --json
```

Note any alternate keys — generated values for those columns must be unique across the run (append the
record index, e.g. `TD-{table}-{i}`).

## 4. ManyToOne (lookup) relationships -> dependency graph

```
GET /api/data/v9.2/EntityDefinitions(LogicalName='<table>')/ManyToOneRelationships
    ?$select=ReferencingAttribute,ReferencingEntityNavigationPropertyName,ReferencedEntity
```

For every lookup attribute found in step 1 that is `ApplicationRequired` or `Recommended`:
- If `ReferencedEntity` is one of the other requested tables -> add a dependency edge (child depends on
  parent).
- If a required lookup has exactly one external target, dynamically add that related table to the plan.
- If a required lookup is polymorphic or cannot be resolved, stop workbook generation and ask the user to
  select the intended target — never silently skip it or fabricate a GUID.
- Optional lookups are included when their target table is part of the requested/generated table set.

Topologically sort the requested tables by these edges. Tables with no incoming edges are Level 0 and can
be generated/committed in parallel; each subsequent level depends on the previous (same leveling pattern as
dv-data's `references/multi-table-fk-import.md`).

## 5. Business rules not visible in metadata

Plugins and classic business rules can enforce constraints metadata won't show (e.g., "email domain must
match account", "end date must be after start date", cross-field conditional requirements). Discover these
defensively, not exhaustively:

1. Before approval, only inspect metadata and existing records — do not create a probe.
2. After the user approves the Excel plan, create **one single approved probe record** per table (not the
   full batch) before committing the rest.
3. If the probe fails with an `HttpError` 400/409, read the message — it usually names the offending field
   or rule. Adjust the generator for that field and re-probe.
4. If satisfying that rule changes any values beyond the already-approved row, stop, regenerate the Excel
   preview, and ask for approval again. Otherwise retain the probe as row 1 of the approved batch and create
   the remaining rows so the final count stays exact.
