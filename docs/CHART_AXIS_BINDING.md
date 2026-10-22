# Chart Axis Binding

Why a chart plots **Total Sales** on the y-axis rather than **ProductKey**,
and the column semantics that make that decision deterministic.

Owning modules: `db/column_semantics.py`,
`api.schemas.ColumnSemanticsOut`, `frontend/src/lib/chartEngine.ts`.

Companion to [`docs/DISPLAY_NAMES.md`](DISPLAY_NAMES.md), which covers the
*SQL* side — making sure a readable name is returned at all. This
document covers what the chart does with the columns once they arrive.

---

## 1. The bug, and why type information could not fix it

"Total sales by product" rendered a chart whose y-axis was `ProductKey`.

The chart engine knew each column's **storage type** — `numeric`, `date`
or `text`, from `agent.result_charting.classify_columns` — and picked the
first `numeric` column as the measure:

```ts
// frontend/src/lib/chartEngine.ts, before
yColumns: numeric.slice(0, 1).map((c) => c.name)
```

**A surrogate key is numeric.** So for `SELECT ProductKey, SUM(SalesAmount)`,
`ProductKey` came first, was numeric, and became the value axis. Worse, a
numeric column was also ineligible as a *category*, so the result had no
sensible x-axis either.

No amount of type information fixes this, because `ProductKey` and
`TotalSales` have the same type. What separates them is **meaning**.

A second instance of the same root cause sat one layer up:
`getChartTypeOptions` counted numeric columns when deciding which chart
types were *valid*, so a key-only result still reported scatter as a
valid chart — and `createDefaultChartOptions`' fallback then produced one.
Both layers are fixed.

---

## 2. Column semantics

`db/column_semantics.py` classifies every result column and ships the
result on `ExecuteResponse.column_semantics`.

| Field | Meaning |
|---|---|
| `name` | Column name exactly as returned (the SQL alias) |
| `label` | Human-readable, e.g. `TotalSales` → "Total Sales" |
| `data_type` | `numeric` / `date` / `text` |
| `semantic_type` | `dimension` / `key` / `measure` / `date` / `percentage` / `currency` / `count` / `text` |
| `number_format` | `plain` / `currency` / `percent` / `integer` |
| `suitable_for_x` | May label a category axis |
| `suitable_for_y` | May be plotted as a value — **false for every key** |
| `hidden_by_default` | Table may hide it unless asked (keys only; data is never dropped) |
| `measure_priority` | How primary a measure is; 0 = never a measure |

### How a key is detected

Two signals, the same pair `db.column_formatting.is_probable_surrogate_key`
already owns:

1. **Schema** — the column is a primary or foreign key per live
   introspection. Reliable, and the one that matters.
2. **Name** — the last token is `key` or `id`. The fallback, because an
   aggregate's alias never traces back to a schema column.

Matching the last *token* rather than a bare suffix is deliberate:
`Valid` is one token and is not literally "id", and `PostalCode` /
`AccountNumber` end in Code/Number. They are not keys.

### Numeric columns that are categories

A **year** is numeric and is a perfectly good x-axis — but plotting
"sales by year" with the year on the y-axis is the same class of error as
plotting a key there. `CalendarYear`, `OrderMonth`, `QuarterNumber` and
friends are therefore `suitable_for_x: true`, `suitable_for_y: false`.

---

## 3. Choosing the measure

`measure_priority` implements the brief's ladder. Higher wins; ties break
on result order, which is the order the SQL named the columns.

| Rung | Matches | Priority |
|---|---|---|
| **What the query ranks by** | any measure in `ORDER BY` | **+1000** |
| Revenue | revenue, sales, amount, turnover | 100 |
| Profit | profit, margin, earnings, income | 90 |
| Quantity | quantity, qty, units, volume | 80 |
| Count | count, orders, transactions, customers | 70 |
| Average | average, avg, mean, median | 60 |
| Percentage | percent, pct, ratio, rate, share | 50 |
| Any other numeric | — | 10 |
| **Key, year, date-part** | — | **0** (never a measure) |

### Why `ORDER BY` is the top rung

The brief's first priority is *"the measure explicitly mentioned in the
user question"*. Reading the question is unreliable — "which customer
bought **frequently**" contains neither "orders" nor "count".

The query already encodes the answer. The generator writes
`ORDER BY TotalOrders DESC` *because* the question asked who bought most
frequently. So the measure the query ranks by **is** the measure the
question asked about, and it is available deterministically from
`agent.sql_validator.describe_sql`'s `SqlShape.order_by`.

Without it, "which customer bought frequently" would default to Total
Sales (priority 100) over Total Orders (70) — which the brief explicitly
calls wrong.

The boost never promotes a key or a year: ranking by `ProductKey` does
not make it a measure.

---

## 4. Axis rules

Implemented in `frontend/src/lib/chartEngine.ts`:

- **`measureColumns(columns)`** — every `suitable_for_y` column, most
  primary first. **Every** y-axis in the engine now comes from here;
  none takes "the first numeric column".
- **`categoryColumns(columns)`** — every `suitable_for_x` column, best
  first: a real date, then a numeric category such as a year, then a text
  dimension.

| Chart type | X | Y |
|---|---|---|
| bar, bar-horizontal, line, area | best category | primary measure |
| bar-stacked, grouped, mixed | best category | top **two** measures |
| pie / doughnut | best category (labels) | primary measure (values) |
| scatter | primary measure | second measure |
| kpi, histogram | — | primary measure |

**Scatter is the one type whose x-axis is a measure** — it plots one
measure against another. A key is excluded from both axes there too.

**No measure means no chart.** `recommendChart` and
`createDefaultChartOptions` return `null`, and the UI shows the table with
a message rather than being handed a chart of identifiers.

### Labels, tooltips and axis titles

`prepareChart`'s `labelOf()` resolves every rendered name through the
column's semantics: the server's friendly label when present
(`TotalSales` → "Total Sales"), otherwise the raw name. **Axis titles,
legend entries and CSV headers all read it**, so none of them shows a raw
column name. Before this they used `series.yColumns[0]` directly, so the
y-axis title read `TotalSales`.

`number_format` defaults the chart's own format from the measure being
plotted, so a currency measure renders as currency without the user
configuring anything. The server's `'integer'` has no chart-option
equivalent and renders as `'plain'`.

Both degrade cleanly: with no semantics, labels fall back to raw names
and the format to `'plain'` — exactly the previous behaviour.

### Two layers of defence

Both are measure-aware, and the second is why a key-only result offers no
chart at all:

1. **Validity** — `getChartTypeOptions` asks whether a *measure* exists,
   not whether a *numeric column* exists.
2. **Selection** — `defaultSeriesForType` and `recommendChart` bind only
   measures to a value axis.

---

## 5. Changing the measure

`ChartCustomizePanel` already offered a y-axis selector; it now lists
friendly labels and, because the result already holds every column, a
switch **re-renders from data in hand and never re-runs the SQL**.
`bar-stacked` and `mixed` additionally expose a second measure.

---

## 6. Fallback when semantics are absent

`column_semantics` is additive and optional. Any path that does not carry
it — an older persisted result, or a caller that has not been updated —
falls back to name patterns inside `inferColumnRoles`:
`looksLikeKeyColumn` plus the numeric-category test. Weaker than the
server's schema knowledge, but it still catches `ProductKey` /
`CustomerID`, which are the shapes that actually produced the bug.

---

## 7. Test coverage

**`frontend/src/lib/chartAxisBinding.test.ts`** — 37 tests. Each scenario
is asserted **twice**, once with server semantics and once without, since
a regression in either path reproduces the bug.

| Scenario | Asserted |
|---|---|
| The reported bug | y = `TotalSales`, never `ProductKey`; key not promoted to x either |
| Total sales by product | x = Product, y = Total Sales, friendly axis title, **values unscaled** |
| Which customer bought frequently | y = Total Orders (the ranked measure); both measures remain selectable |
| Year-wise sales | numeric year is x, never y |
| Top products by revenue and quantity | defaults to Revenue; switching to Quantity needs no new query |
| Only keys | **no chart offered at all** |
| Key detection by name | 5 key shapes, 6 non-key shapes including `PostalCode`, `AccountNumber`, `Valid` |
| Axis titles and legends | y-title is "Total Sales" not `TotalSales`; pie labels the dimension and values the measure |
| Number formatting | a currency measure defaults the chart to currency; `'integer'` renders plain; no semantics stays plain |
| No regression | plain results, scatter, stacked bar unchanged |

**`tests/test_column_semantics.py`** — 43 tests: key classification,
both detection signals, the full priority ladder, `ORDER BY` boosting
(including that it never promotes a key or a year), `ORDER BY` parsing in
its real shapes, number formats, labels, the explicit wire shape, and
unaffected ordinary results.

---

## 8. Known limitations

- **The ladder is name-based.** A measure called `Widgets` scores as a
  generic numeric (priority 10). It is still a valid y-axis and still
  beats every key — it just loses to a column named `Revenue` in the same
  result. `ORDER BY` overrides this whenever the query ranks by it.
- **`ORDER BY` an expression contributes nothing.** `ORDER BY SUM(x) DESC`
  matches no column name, so the ladder stays in charge. Safe: it leaves
  a sensible default rather than guessing.
- **Semantics are computed in `/execute` only.** That is where charts are
  built. The `/ask` preview path falls back to name patterns.
- **Not verified against a live browser render.** The engine's output is
  asserted in tests (axis columns, dataset values, labels); the visual
  result is not screenshot-tested.
