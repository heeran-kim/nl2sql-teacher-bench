[English](README.md) | [한국어](README.ko.md) | [中文](README.zh.md)

# nl2sql-teacher-bench

A small, model-agnostic benchmark for comparing **zero-shot natural-language-to-SQL
generation quality** across any LLM served by [Ollama](https://ollama.com).

## What this is for

If you're building an NL2SQL fine-tuning pipeline, you typically need a
"teacher" model: something to generate the SQL labels for your training
set (often because you don't have enough hand-labeled examples). Which
model should you use for that?

That's a different question than "which model should I deploy," and it's
worth benchmarking separately:

- The teacher runs once, offline, to produce training data. Inference cost
  and latency barely matter.
- Only raw generation quality on your schema and your query style matters.
- The best teacher is not necessarily the model you'd fine-tune and ship --
  it can be arbitrarily large or slow, as long as it runs *at all*.

This tool runs a set of candidate models against a held-out test set of
(prompt, reference SQL) pairs and reports how each one scores, so you can
pick a teacher based on evidence instead of a hunch.

It does **not** do fine-tuning, prompt engineering, or agentic/multi-step
SQL generation -- it's deliberately narrow: one prompt in, one SQL query
out, scored against a reference.

## Requirements

- Python 3.9+
- [Ollama](https://ollama.com) installed and running (locally, or reachable
  via `--ollama-host`)
- The models you want to compare already pulled into Ollama

```bash
pip install -r requirements.txt
```

## Quickstart

```bash
ollama pull qwen3:14b
ollama pull llama3.1:8b

python bench.py \
  --models qwen3:14b llama3.1:8b \
  --data data/example.jsonl \
  --schema data/example_schema.sql \
  --dialect mysql
```

This seeds an in-memory SQLite database from the bundled example schema
(a generic customers/orders schema, no relation to any real project),
runs both models over the bundled example questions, scores every answer
(including **execution accuracy** -- see below), and writes `results.md`.

`--schema` is optional. Without it you still get text-based scoring
(exact match, parses, table/column overlap) -- just skip that flag and
the `{{schema}}` placeholder described next.

## Using your own test data

### Test set format

JSONL, one example per line, two required fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `prompt` | string | The complete instruction sent to the model -- system context, schema, question, formatting instructions, few-shot examples, whatever you want. Sent verbatim as a single user message. |
| `reference_sql` | string | The gold/expected SQL query for that prompt. |

```json
{"prompt": "You are a SQL generator...\n\nSchema:\n{{schema}}\n\nQuestion: ...", "reference_sql": "SELECT ..."}
```

### Schema: one source of truth, not two

If every example's prompt needs the same schema, don't paste it into every
line by hand -- that's a drift hazard the moment the schema changes. Put
the literal token `{{schema}}` in your prompts instead, and pass the real
schema once via `--schema path/to/schema.sql`. Before sending anything to
a model, `bench.py` substitutes `{{schema}}` with the file's contents, so
there's exactly one place your schema lives.

`--schema` does two things at once:

1. Fills in `{{schema}}` in every prompt (skip this and write the schema
   directly into your prompts if you don't need it -- e.g. per-example
   schemas, or no schema at all).
2. Builds an in-memory SQLite database from it and seeds it with random
   data, which unlocks execution-accuracy scoring (next section).

The schema file itself is just standard `CREATE TABLE` statements, in
whatever dialect you pass via `--dialect`:

```sql
CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    name VARCHAR(100),
    email VARCHAR(100)
);
CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER,
    status VARCHAR(20),
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);
```

Single-column `PRIMARY KEY` and `FOREIGN KEY` are recognized, inline or as
table-level constraints. Composite keys aren't specially handled -- only
the first column of a composite key is used for seeding.

### Running it

```bash
python bench.py \
  --models qwen3:14b sqlcoder:7b \
  --data path/to/your_testset.jsonl \
  --schema path/to/schema.sql \
  --dialect postgres
```

Use `--dialect` to match whatever SQL your schema and reference queries
are written in (any [sqlglot dialect name](https://sqlglot.com/sqlglot/dialects/dialect.html) --
e.g. `mysql`, `postgres`, `sqlite`, `snowflake`, `bigquery`; default is
sqlglot's generic dialect). This affects parsing/scoring/DDL conversion
only, not what gets sent to the model.

## What gets measured

For each example, the model's raw output is passed through `extract_sql()`
(strips markdown fences and any reasoning preamble some models emit before
the actual query), then scored against the reference.

**Always computed:**

- **valid** -- does the generated SQL parse at all, under `--dialect`?
- **table_match** -- same set of tables referenced as the reference
  (order-independent)?
- **col_overlap** -- fraction of the reference's selected columns also
  present in the generated query's SELECT list.
- **exact_match** -- byte-for-byte match after stripping whitespace.
  Strict: logically equivalent SQL with different formatting, column
  order, or aliasing counts as a miss.

**Computed only when `--schema` is given:**

- **execution_match** -- runs both the reference and generated query
  against the same seeded database and compares result sets (as
  multisets of rows, order-independent). This is the standard evaluation
  method used by academic NL2SQL benchmarks (Spider, BIRD), because it
  correctly credits a query that's written differently but semantically
  equivalent -- exactly the case `exact_match` gets wrong.
- **generated_executable** / **reference_executable** -- did the query run
  without a database error at all (regardless of whether the result
  matched)?

Treat `execution_match` as the primary signal once you have a schema
available, and `exact_match` as a strict lower bound, not the real
picture.

### Limitations of execution scoring

- **Data is random, not realistic.** Seeded values are type-appropriate
  random noise, not a copy of your real data -- with one exception: a
  column declared as `ENUM(...)` or with a `CHECK (col IN (...))`
  constraint is sampled from that declared value set, so a filter like
  `WHERE status = 'COMPLETED'` has a real chance of matching. Without a
  declared value set, that same filter will usually match zero rows
  against random text, so both queries return an empty set -- which
  trivially "matches" without actually exercising the filter. If your
  schema doesn't declare its valid values, either add a `CHECK` constraint
  to your local copy of the schema (harmless -- it's only used to build
  the synthetic test database, not your real one), write test questions
  whose filters key off generated ranges instead (IDs 1..N, dates within
  the last ~2 years), or seed the database yourself and call
  `execution.compute_execution_match()` directly with your own connection
  for full control.
- **SELECT-only.** Non-SELECT statements aren't executed against the seeded
  database (by design, so one bad model output can't mutate data that other
  examples in the same run depend on).
- **SQLite is the execution target regardless of your schema's dialect.**
  Standard DDL/DML transpiles fine via sqlglot, but sufficiently
  dialect-specific SQL (exotic window functions, JSON operators, date
  arithmetic) may not translate perfectly. If a result looks wrong, check
  `results.md`'s per-example detail and the `generated_error`/reference
  error before trusting the number.

## Candidate models

A starting set of models worth benchmarking as an NL2SQL teacher, split
into general-purpose LLMs and NL2SQL-specialized checkpoints. Pull
commands assume Ollama; for models without an official Ollama library
tag, Ollama can pull GGUF files directly from any Hugging Face repo with
`ollama pull hf.co/<repo>:<quant>` -- no manual `Modelfile` needed. If a
listed quant tag 404s, check the repo's file list on Hugging Face for the
exact name (community quantizers don't always use the same tag scheme).

### General-purpose

| Model | Size (active) | Pull command |
| --- | --- | --- |
| Qwen3-14B | 14.8B dense | `ollama pull qwen3:14b` |
| Qwen3-30B-A3B | 30B total / 3B active (MoE) | `ollama pull qwen3:30b-a3b` |
| Qwen3-Coder-30B-A3B | 30B / 3.3B active (MoE) | `ollama pull qwen3-coder:30b-a3b` |
| Granite 4.0 H-Small | 32B / 9B active (MoE) | `ollama pull ibm/granite4:small-h` |

### NL2SQL-specialized

| Model | Size (active) | Pull command |
| --- | --- | --- |
| OmniSQL-7B | 7B dense | `ollama pull hf.co/mradermacher/OmniSQL-7B-GGUF:Q4_K_M` |
| OmniSQL-14B | 14B dense | `ollama pull hf.co/mradermacher/OmniSQL-14B-GGUF:Q4_K_M` |
| OmniSQL-32B | 33B dense | `ollama pull hf.co/mradermacher/OmniSQL-32B-i1-GGUF:Q4_K_M` |
| SQLCoder-7B-2 (defog, CodeLlama-based) | 7B dense | `ollama pull pxlksr/defog_sqlcoder-7b-2:Q4_K_M` |
| Llama-3-SQLCoder-8B (defog) | 8B dense | `ollama pull hf.co/QuantFactory/llama-3-sqlcoder-8b-GGUF:Q4_K_M` |
| SQLCoder2-15B (defog, StarCoder-based) | 15B dense | `ollama pull hf.co/TheBloke/sqlcoder2-GGUF:Q4_K_M` |
| Arctic-Text2SQL-R1-7B (Snowflake) | 7B dense | `ollama pull a-kore/Arctic-Text2SQL-R1-7B` |
| XiYanSQL-QwenCoder-7B | 7B dense | `ollama pull hf.co/mradermacher/XiYanSQL-QwenCoder-7B-2504-GGUF:Q4_K_M` |
| XiYanSQL-QwenCoder-14B | 14B dense | `ollama pull hf.co/visualbruno/XiYanSQL-QwenCoder-14B-2502-Q5_K_M-GGUF` |
| XiYanSQL-QwenCoder-32B | 32B dense | `ollama pull hf.co/mradermacher/XiYanSQL-QwenCoder-32B-2504-GGUF:Q4_K_M` |

These are starting points, not a fixed list -- any model Ollama can run is
a valid candidate. See "Adding a candidate model" below. Note that
Arctic-Text2SQL-R1 and XiYanSQL-QwenCoder don't necessarily publish every
size publicly at all times -- check each org's Hugging Face page for the
current lineup.

A version trap worth flagging: the official Ollama library tags
`sqlcoder:7b` and `sqlcoder:15b` are defog's **original, older** SQLCoder
checkpoints (Mistral-7B-based and the first StarCoder-15B release,
respectively) -- not the improved `sqlcoder-7b-2` (CodeLlama-based) or
`sqlcoder2` versions listed above, which only exist as community-published
tags/GGUFs. The short official tag name doesn't imply it's the latest
version; check what a tag actually points to before assuming.

## Reasoning about your own hardware

Two things determine whether a model is practical to run, and they're
independent -- don't rule a model out on total size alone:

- **Memory footprint** (must fit in VRAM+RAM combined): scales with
  *total* parameters at whatever quantization you use. Ollama
  automatically splits a model's layers across GPU and system RAM if it
  doesn't fully fit in VRAM -- it'll still run, just slower for the
  CPU-resident layers.
- **Compute cost per token** (governs speed): scales with *active*
  parameters per forward pass. Dense models activate 100% of their
  parameters every token. Mixture-of-Experts (MoE) models -- named like
  "30B-A3B" (30B total, 3B active) -- only activate a fraction, so they
  carry much more total capacity at a fraction of the compute cost of an
  equivalently-sized dense model.

Rule of thumb: a quantized model's size in GB is roughly `total params
(B) x bits_per_weight / 8` (e.g. 14B at 4-bit is roughly 7GB, plus some
overhead). If that's well under your combined VRAM+RAM, it'll run;
whether it's *fast* depends on how much had to spill from VRAM into
system RAM and how many params are active per token.

Since the teacher role only runs once, offline, favor generation
*quality* over speed when picking hardware trade-offs -- a slow-but-better
teacher is usually worth the wait.

## Adding a candidate model

Any tag Ollama can serve works:

```bash
# Official Ollama library
ollama pull <tag>

# Any GGUF repo on Hugging Face, no Modelfile needed
ollama pull hf.co/<user>/<repo>:<quant>
```

Then just include the tag in `--models`.

## Output

`bench.py` writes a single Markdown report (`results.md` by default,
override with `--output`) with:

- A summary table (execution match and executable rate if `--schema` was
  given; exact match, valid, table match, column overlap average, and
  average latency always) for every model tested.
- A per-example detail table showing each model's generated SQL side by
  side with the reference, for manual review of near-misses.

It also prints a live per-example progress line and a final summary to
stdout as it runs.

## Repository layout

```
bench.py           CLI entry point
metrics.py         Text-based scoring (valid / table match / column overlap / exact match)
db_builder.py       Schema parsing + in-memory SQLite database seeding
execution.py        Execution-accuracy scoring against the seeded database
ollama_client.py    Minimal Ollama /api/chat client
data/example.jsonl        Demo test set (generic schema, not tied to any real project)
data/example_schema.sql   Demo schema for the above
```

Everything else under `data/` is git-ignored by default -- that's where
your own (possibly private) schema and test set belong.
