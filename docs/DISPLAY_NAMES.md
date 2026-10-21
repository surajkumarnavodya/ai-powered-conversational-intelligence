# Readable Business Names

The authoritative document for why a result shows **Mountain-200 Black, 46**
rather than **ProductKey 310**, and for the metadata, SQL enforcement and
chart rules that make that happen.

Owning modules: `db/display_names.py`, `config/display_names.py` +
`config/display_names.yaml`, `agent/display_name_guard.py`,
`agent.nodes.enforce_display_names_node`,
`db.schema_introspection.parse_ddl_table_info`.

---

## 1. The problem, and what it actually was

Questions grouped by an entity came back labelled by the surrogate key:
"total sales by product" returned `ProductKey`, "which customer bought
frequently" returned `CustomerKey`.

The investigation found the obvious explanation to be **wrong**.
`agent/llm_client.py`'s generation prompt has instructed the model to do
the right thing for a long time:

> *If the final result would otherwise show only a surrogate key column
> (a column named like '...Key' or '...ID'), join in and SELECT a
> human-readable descriptive column from that same dimension instead.*

So this was never a missing instruction. It was an **unenforced** one —
local models follow it inconsistently, and nothing downstream checked.
CLAUDE.md §4.3 is explicit that the model may propose but a deterministic
check must decide; there was no such check for display names.

Four contributing causes, all verified in code:

| Cause | Where |
|---|---|
| The prompt rule was advisory, with no verification | `agent/llm_client.py::_system_prompt` |
| Nothing knew *which* column is an entity's name | `TableSchemaInfo` has columns/PK/FK, no display column, no fact/dimension split |
| The key-hiding helper was never wired into the live path | `db/column_formatting.py::get_display_columns`, used only by `scripts/profile_pipeline.py` |
| Chart labels bound to the first text column | `result_charting.py`: `x_column=text_cols[0]` — and a *numeric* `ProductKey` classified as a measure, producing a **scatter plot of key against sales** |

---

## 2. The metadata layer

`db/display_names.py` answers one question: *what is the readable name
for this key, and which declared foreign key reaches it?*

### Classification

`classify_table` returns `fact`, `dimension` or `unknown`. Name patterns
first (`DimProduct`, `FactSales` — a schema using them is telling you the
answer), then structure: three or more foreign keys means a fact table; a
single-column primary key plus a readable column means a dimension.

`unknown` is a real answer. Such a table is never used as a name source,
which is the safe direction.

### Choosing the display column

In order, first match wins:

1. **A `config/display_names.yaml` entry.** Always wins. An entry naming
   a column the live table does not have is **ignored with a warning** —
   the database stays the source of truth for what exists, so a stale
   entry can never produce SQL referencing a dropped column.
2. **A composite person name** — `FirstName` + `LastName` and its
   variants. Checked before single columns because returning only
   `LastName` for "sales by employee" is a worse answer than the full
   name.
3. **Name-shaped patterns**, specific before generic:
   `^english\w*name$` → `^\w+name$` → `^name$` → `title` → `label` →
   `description`. Localised names come first because AdventureWorks-style
   schemas call the user-facing column `EnglishProductName`, and matching
   `^\w+name$` first could pick a different one.
4. **The table's own core name.** `DimSalesTerritory`'s label is
   `SalesTerritoryRegion`, which is not name-shaped at all. The principle
   is that *a column named after its own table describes that entity*.
   Narrower than adding more patterns, and constrained three ways: it
   runs only after every pattern fails, the column must be a text type
   (so `OrderDate` on a `DimOrder` cannot win), and key-shaped names are
   excluded. Shortest candidate wins.

**Always excluded:** anything ending in `Key`, `ID`, `GUID`, `Code`, or
`AlternateKey`. `ProductAlternateKey` is a text column that would
otherwise match a name pattern; it is a key.

**Whole dimensions excluded: date/time and system tables.** A date key is
a *grain*, not an entity reference, and a date is already readable. This
was found by running the resolver against a real AdventureWorks schema
rather than a fixture: `DimDate` carries `EnglishMonthName`, which
matches the name patterns, so `OrderDateKey` resolved to it — and
"sales by order date" would have been silently rewritten into "sales by
month name", collapsing every year together and changing what the query
answers. Exactly the silent wrongness this guard exists to prevent,
arriving from the inside.

A date key is therefore **skipped silently** — neither repaired nor
disclosed. There is nothing to apologise for, and a notice would put a
misleading caveat on a perfectly good answer. System/catalog tables
(`sysdiagrams`, `dtproperties`) are excluded for a duller reason: they
are not business entities. An explicit `config/display_names.yaml` entry
still overrides both exclusions — that is a decision rather than an
inference.

**No match returns `None`**, which is the point — the caller must be able
to tell "no readable name exists" from "here is one".

### Composite names

The concatenation rule is fixed here rather than left to the model:

```sql
LTRIM(RTRIM(CONCAT(COALESCE(c.FirstName, ''), ' ', COALESCE(c.LastName, ''))))
```

- **`COALESCE` on every part.** `NULL + 'x'` is NULL in every supported
  engine, so an unguarded concatenation makes an employee with no
  recorded first name **vanish from the result** rather than appear under
  their last name.
- **`LTRIM(RTRIM(...))`, not `TRIM`.** `TRIM` needs SQL Server 2017+.
- Separator is configurable per table; default `" "`.

### Relationships

Every join comes from a **declared foreign key** read from live
introspection. `db/relationship_inference.py` exists for *suggesting*
undeclared relationships to a human and is deliberately **not** consulted
here: joining two columns because their names look alike is the specific
mistake that produces a query which runs, returns rows, and is silently
wrong. Single-column foreign keys only — a composite FK needs a
multi-column `ON` clause, and building one automatically is a guess.

---

## 3. SQL enforcement

`agent.nodes.enforce_display_names_node` runs between
`review_metric_conformance` and `validate_sql`:

```
generate_sql → review_sql → review_metric_conformance
             → enforce_display_names → validate_sql → estimate_cost → execute_sql
```

Placement matters: anything the guard rewrites **still passes through
`agent/sql_validator.py`**, so a repair can never widen what the SQL is
permitted to do.

### Three outcomes

| Outcome | When | What happens |
|---|---|---|
| **`repaired`** | A key labels rows, and exactly one declared FK reaches a dimension with a readable column | Join added, key swapped for the name, `AS Product` alias applied. Proceeds to validation. |
| **`needs_retry`** | The shape is beyond a provably safe repair (no declared FK, unresolvable alias) | Becomes error feedback for the next `generate_sql` attempt, naming the exact table, join column and display column. Shares the one `MAX_RETRIES` budget — never a second loop. |
| **`no_display_name`** | The key reaches a dimension with no readable column at all | Nothing repaired, nothing retried. The key is shown honestly and `display_name_notice` explains why. |

A fourth, commonest case is `ok`: the query already selects names, or has
no entity label at all, and is returned byte-for-byte unchanged.

### What is never touched

- **A key inside an aggregate.** `COUNT(DISTINCT f.CustomerKey)` counts
  customers; swapping it would change what the query computes. Only keys
  in a *label* position — a bare projection or a `GROUP BY` term — are
  candidates.
- **Measures.** `SUM(f.SalesAmount) AS TotalSales` survives verbatim.
- **Row limits and ordering.** `TOP 10 ... ORDER BY TotalSales DESC` is
  preserved.
- **An already-joined dimension.** The existing alias is reused. A second
  copy would multiply rows and silently change every measure.
- **Non-entity grouping columns.** `d.CalendarYear` in "year-wise sales
  by customer" stays exactly as written.
- **Unparseable SQL.** Left to `agent/sql_validator.py`, which owns that
  verdict.

### Worked example

Generated:

```sql
SELECT f.ProductKey, SUM(f.SalesAmount) AS TotalSales
FROM FactInternetSales f
GROUP BY f.ProductKey ORDER BY TotalSales DESC;
```

Executed:

```sql
SELECT pro.EnglishProductName AS Product, SUM(f.SalesAmount) AS TotalSales
FROM FactInternetSales AS f
INNER JOIN DimProduct AS pro ON f.ProductKey = pro.ProductKey
GROUP BY pro.EnglishProductName ORDER BY TotalSales DESC;
```

`INNER JOIN`, because a fact row must have a matching dimension row for
its measure to be attributable at all; a `LEFT JOIN` would add an
unlabelled bucket.

### Where the schema comes from

The graph carries retrieved schema as DDL *text*
(`AgentState.schema_tables`), not as structured introspection. Rather than
add a database round trip per question,
`db.schema_introspection.parse_ddl_table_info` rebuilds the structure from
the text `render_ddl` already emitted. The parser lives beside the
renderer because the two share one format.

Only the tables retrieved **for this question** are considered, so the
guard can never reach for a relationship the model was not itself shown.

---

## 4. Result mapping, charts and the answer

### Column labels

`db/column_formatting.py::format_column_label` (and its frontend mirror
`columnFormatting.ts`) already turn `TotalSales` into *Total Sales* and
`OrderQty` into *Order Quantity*, using
`config/column_labels.py::ABBREVIATION_EXPANSIONS`. That layer is
unchanged — the repair means it now receives a name column to label
rather than a key.

### Charts

`recommend_chart` takes the result's key columns and **excludes them from
both axes**. Two real failures this closes:

- a numeric `ProductKey` classified as `"numeric"`, so
  `(ProductKey, TotalSales)` fell through to the two-measure branch and
  rendered a **scatter plot of key against sales** — a cloud of points
  whose x-axis means nothing;
- a string key would have been chosen as the bar chart's category axis
  purely for being the first text column.

With a name present, the name becomes the category axis and the measure
the value axis. With only a key left (the cases the guard could not
repair), **no chart is recommended** — the table still shows the key
honestly, but a chart built on it would not be honest at all.

Omitting the argument preserves the previous behaviour exactly, which is
what keeps existing callers unchanged.

### Natural-language answer

The insight step reads the governed result rows. Because the SQL now
returns names, the answer uses names — "The highest-selling product was
Mountain-200 Black, 46…" rather than a key. No separate formatting layer
was added: one source for the label is the point (CLAUDE.md §4.8).

### API surface

Two additive, optional fields on `AskResponse`:

| Field | Meaning |
|---|---|
| `display_names_repaired` | Key columns rewritten into a joined name, e.g. `["ProductKey"]`. Recorded so a reader can see the SQL shown is not byte-for-byte what the model wrote, and why. |
| `display_name_notice` | Set only when a label is still a key because no readable column exists. An honest disclosure, never a fabricated label. |

---

## 5. Configuration

| Variable | Default | Effect |
|---|---|---|
| `ENABLE_DISPLAY_NAME_ENFORCEMENT` | `true` | `false` makes the node a pass-through; only the prompt's advisory rule applies. |

`config/display_names.yaml` ships as an empty template. You usually do not
need it — inference handles the common shapes. Declare a table when
inference would pick wrong:

```yaml
tables:
  DimAccount:
    columns: [AccountDescription]   # not name-shaped; inference would miss it
    key_column: AccountKey

  DimCustomer:
    columns: [FirstName, LastName]
    key_column: CustomerKey
    separator: " "
```

---

## 6. Test coverage

`tests/test_display_names.py` — 47 tests, fully mocked against an
AdventureWorks-shaped schema. All nine scenarios the brief names:

| Scenario | Asserted |
|---|---|
| Total sales by product | joins `DimProduct`, selects `EnglishProductName`, groups by the name |
| Which customer bought frequently | customer name **and both** measures (`TotalOrders`, `TotalSales`) intact |
| Top 10 products by sales | `TOP 10` and `ORDER BY` preserved |
| Sales by customer | no `CustomerKey` left in `GROUP BY` |
| Sales by employee | composite `FirstName`/`LastName` |
| Sales by territory | `SalesTerritoryRegion` via the core-name rule |
| Sales by category and subcategory | **both** entities named in one query |
| Year-wise sales by customer | `CalendarYear` untouched, customer named |
| No matching dimension name | `no_display_name`, SQL unchanged, disclosure returned |

Plus a `TestDateDimensionsAreNeverLabelled` class pinning the rule
above: a date dimension resolves to nothing, a date key is neither
indexed nor disclosed, grouping by one is left byte-for-byte alone, a
query mixing a date and an entity repairs *only* the entity, and a
system table is never treated as an entity.

Plus: no guessed join, specific retry feedback, aggregates untouched,
correct SQL left byte-for-byte alone, no double join, measures preserved,
every repaired statement re-passes the SELECT-only allowlist, and no
write statement is ever produced.

---

## 7. Known limitations

- **Single-column foreign keys only.** A composite FK is reported for
  retry rather than joined automatically.
- **One `SELECT` root.** A statement rooted in a `UNION` or a CTE is left
  alone rather than half-repaired.
- **Subquery sources are not repaired.** A key selected from a derived
  table has no schema FK to resolve against.
- **Only tables retrieved for this question.** If schema retrieval did not
  return the dimension, the guard cannot use it. This is deliberate — it
  bounds the guard to what the model itself was shown.
- **Verified against the live AdventureWorks schema** (36 tables): 10
  facts, 18 dimensions, 8 correctly-unknown views/system tables, and 20
  key columns resolved — including labels inference had to work for
  rather than pattern-match, such as `DimAccount.AccountDescription`,
  `DimGeography.EnglishCountryRegionName` and `DimSalesTerritory
  .SalesTerritoryRegion`. Degenerate dimensions (`SalesOrderNumber`)
  correctly reach the "no readable name" disclosure.
- **Display-column inference is pattern-based.** It is right on the
  shapes tested here and on AdventureWorks-style schemas; a schema with
  unusual conventions should use `config/display_names.yaml`. The choice
  is recorded as `source="inferred"` vs `"config"` so an operator can see
  which entities still rest on inference.
- **Not verified against a live database.** All tests are mocked, per
  this repo's testing rule. `python scripts/integration_test.py` is the
  manual path against real data.
