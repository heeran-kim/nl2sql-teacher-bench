#!/usr/bin/env python3
"""
nl2sql-teacher-bench: compare zero-shot NL2SQL generation quality across
any set of Ollama-served models.

    python bench.py --models qwen3:14b llama3.1:8b --data data/example.jsonl

See README.md for the test-data format, the metrics this reports, and how
to add a new candidate model.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

from db_builder import build_database, create_tables
from execution import aggregate_execution, compute_execution_match
from metrics import aggregate, compute_metrics, extract_sql
from ollama_client import chat


def run_seed_script(path: Path, conn) -> None:
    """Load `path` as a module and call its `seed(conn)` function.

    This is the supported way to seed the execution database with your own
    domain-specific rows instead of random data -- e.g. known IDs your test
    questions reference, or realistic enum-free string values. See
    data/seed_aceso.py in this repo for a worked example.
    """
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load '{path}' as a Python module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "seed"):
        raise ValueError(f"{path} must define a seed(conn) function")
    module.seed(conn)
    conn.commit()


def load_dataset(path: Path) -> list[dict]:
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            missing = [k for k in ("prompt", "reference_sql") if k not in record]
            if missing:
                raise ValueError(f"{path}:{line_num}: missing required field(s) {missing}")
            records.append(record)
    if not records:
        raise ValueError(f"{path}: no examples found")
    return records


def run_model(
    model: str,
    dataset: list[dict],
    host: str,
    dialect: str,
    timeout: int,
    conn=None,
    think: bool | None = None,
    num_ctx: int = 16384,
) -> list[dict]:
    results = []
    for i, example in enumerate(dataset, start=1):
        start = time.time()
        try:
            raw = chat(model, example["prompt"], host=host, timeout=timeout, think=think, num_ctx=num_ctx)
        except Exception as e:
            elapsed = time.time() - start
            print(f"  [{i}/{len(dataset)}] ERROR: {e} ({elapsed:.1f}s) -- counted as a miss, continuing")
            failed = {
                "generated": "",
                "raw": "",
                "seconds": elapsed,
                "valid": False,
                "table_match": False,
                "col_overlap": 0.0,
                "exact_match": False,
                "error": str(e),
            }
            if conn is not None:
                failed.update(
                    {
                        "reference_executable": False,
                        "generated_executable": False,
                        "execution_match": False,
                        "reference_error": None,
                        "generated_error": str(e),
                    }
                )
            results.append(failed)
            continue
        elapsed = time.time() - start
        generated = extract_sql(raw)
        scored = compute_metrics(example["reference_sql"], generated, dialect=dialect)
        progress = (
            f"  [{i}/{len(dataset)}] valid={scored['valid']} "
            f"table_match={scored['table_match']} exact_match={scored['exact_match']}"
        )
        if conn is not None:
            exec_scored = compute_execution_match(conn, example["reference_sql"], generated, dialect)
            scored.update(exec_scored)
            progress += f" execution_match={exec_scored['execution_match']}"
        results.append({"generated": generated, "raw": raw, "seconds": elapsed, **scored})
        print(progress + f" ({elapsed:.1f}s)")
    return results


def escape_cell(text: str, limit: int | None = None) -> str:
    text = text.replace("\n", " ").replace("|", "\\|").strip()
    if limit is not None and len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def build_report(dataset: list[dict], all_results: dict[str, list[dict]], has_execution: bool) -> str:
    n = len(dataset)
    models = list(all_results.keys())

    summaries = {}
    for model, results in all_results.items():
        summary = aggregate(results)
        summary["avg_latency"] = sum(r["seconds"] for r in results) / len(results) if results else 0.0
        if has_execution:
            summary.update(aggregate_execution(results))
        summaries[model] = summary

    lines = ["# NL2SQL Teacher Comparison", "", f"Test examples: {n}", "", "## Summary", ""]
    lines.append("| Metric | " + " | ".join(models) + " |")
    lines.append("|" + "---|" * (len(models) + 1))

    def row(label: str, fmt) -> None:
        lines.append("| " + label + " | " + " | ".join(fmt(summaries[m]) for m in models) + " |")

    if has_execution:
        row("Execution match", lambda s: f"{s['execution_match']}/{n}")
        row("Generated SQL executable", lambda s: f"{s['generated_executable']}/{n}")
    row("Exact match", lambda s: f"{s['exact_match']}/{n}")
    row("SQL valid (parses)", lambda s: f"{s['valid']}/{n}")
    row("Table match", lambda s: f"{s['table_match']}/{n}")
    row("Column overlap (avg)", lambda s: f"{s['col_overlap_avg']:.0%}")
    row("Avg latency", lambda s: f"{s['avg_latency']:.1f}s")
    lines.append("")
    if has_execution:
        lines.append(
            "> Execution match is the primary signal here: it runs both the "
            "reference and generated query against the same seeded database "
            "and compares result sets, so it correctly credits queries that "
            "are written differently but semantically equivalent. Exact "
            "match is still shown for reference, but treat it as a strict "
            "lower bound, not the real picture."
        )
    else:
        lines.append(
            "> Exact match is strict: logically equivalent SQL with different "
            "formatting, column order, or aliasing counts as a miss. Pass "
            "--schema to additionally score execution match, which doesn't "
            "have this problem. Use the per-example detail below (and the "
            "other metrics) for the full picture in the meantime."
        )
    lines.append("")

    lines.append("## Per-example detail")
    lines.append("")
    lines.append("| # | Prompt | Reference SQL | " + " | ".join(models) + " |")
    lines.append("|" + "---|" * (len(models) + 3))
    for i, example in enumerate(dataset):
        cells = [
            str(i + 1),
            escape_cell(example["prompt"], limit=80),
            f"`{escape_cell(example['reference_sql'])}`",
        ]
        for model in models:
            cells.append(f"`{escape_cell(all_results[model][i]['generated'])}`")
        lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--models", nargs="+", required=True, help="Ollama model tags to compare")
    parser.add_argument(
        "--data", type=Path, default=Path("data/example.jsonl"), help="Path to a JSONL test set"
    )
    parser.add_argument("--ollama-host", default="http://localhost:11434")
    parser.add_argument(
        "--dialect",
        default="",
        help="sqlglot dialect name to parse SQL with (e.g. mysql, postgres, sqlite, "
        "snowflake, bigquery) -- match this to your reference queries' dialect. "
        "Default is sqlglot's generic dialect.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Per-request timeout, in seconds. Reasoning models can take a while on complex "
        "prompts -- raise this rather than reaching for --no-think if a run times out.",
    )
    parser.add_argument(
        "--num-ctx",
        type=int,
        default=16384,
        help="Context window size passed to Ollama. Defaults to 16384 to prevent "
        "reasoning tokens from exhausting the context window.",
    )
    parser.add_argument(
        "--no-think",
        action="store_true",
        help="Disable the internal thinking step on reasoning-capable models (e.g. Qwen3) for "
        "faster, cheaper responses. Off by default -- quality matters more than speed for a "
        "teacher (it only runs once, offline), and this would unfairly cap models built to "
        "reason their way to a correct answer. Useful for quick iteration while debugging a "
        "test set, not for a real comparison.",
    )
    parser.add_argument("--output", type=Path, default=Path("results.md"))
    parser.add_argument(
        "--schema",
        type=Path,
        default=None,
        help="Path to a SQL file of CREATE TABLE statements (in --dialect). When given, "
        "builds an in-memory SQLite database seeded with random data and additionally "
        "scores every example by execution match -- see README.md for details.",
    )
    parser.add_argument(
        "--seed-rows", type=int, default=20, help="Rows of random data to generate per table (with --schema)"
    )
    parser.add_argument("--seed", type=int, default=0, help="Random seed for synthetic data (with --schema)")
    parser.add_argument(
        "--seed-script",
        type=Path,
        default=None,
        help="Path to a Python file defining seed(conn) to populate the execution database "
        "yourself instead of --schema's random fill (requires --schema, for table creation). "
        "Use this when your test questions reference specific known values -- see "
        "data/seed_aceso.py for a worked example.",
    )
    args = parser.parse_args()

    dataset = load_dataset(args.data)
    print(f"Loaded {len(dataset)} examples from {args.data}\n")

    if args.seed_script is not None and args.schema is None:
        parser.error("--seed-script requires --schema (used to create the tables)")

    conn = None
    if args.schema is not None:
        schema_sql = args.schema.read_text(encoding="utf-8")
        if args.seed_script is not None:
            conn, _tables = create_tables(schema_sql, args.dialect)
            run_seed_script(args.seed_script, conn)
            print(f"Seeded an in-memory SQLite database from {args.schema} using {args.seed_script}\n")
        else:
            conn = build_database(schema_sql, args.dialect, seed_rows=args.seed_rows, seed=args.seed)
            print(f"Seeded an in-memory SQLite database from {args.schema} ({args.seed_rows} random rows/table)\n")
        for example in dataset:
            example["prompt"] = example["prompt"].replace("{{schema}}", schema_sql)

    all_results: dict[str, list[dict]] = {}
    for model in args.models:
        print(f"--- {model} ---")
        try:
            all_results[model] = run_model(
                model,
                dataset,
                args.ollama_host,
                args.dialect,
                args.timeout,
                conn=conn,
                think=(False if args.no_think else None),
                num_ctx=args.num_ctx,
            )
        except Exception as e:
            print(f"ERROR: {model} failed entirely ({e}) -- excluding it and continuing with the rest\n")
            continue
        print()

        # Save after each model to preserve results if a later model fails.
        report = build_report(dataset, all_results, has_execution=conn is not None)
        args.output.write_text(report, encoding="utf-8")
        print(f"({len(all_results)}/{len(args.models)} models done -- report updated at {args.output})\n")

    report = build_report(dataset, all_results, has_execution=conn is not None)
    args.output.write_text(report, encoding="utf-8")
    print(f"Report written to {args.output}\n")

    print("Summary:")
    for model, results in all_results.items():
        agg = aggregate(results)
        n = agg["n"]
        line = (
            f"  {model}: exact_match={agg['exact_match']}/{n} valid={agg['valid']}/{n} "
            f"table_match={agg['table_match']}/{n} col_overlap={agg['col_overlap_avg']:.0%}"
        )
        if conn is not None:
            exec_agg = aggregate_execution(results)
            line += f" execution_match={exec_agg['execution_match']}/{n}"
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
