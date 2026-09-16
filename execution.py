"""Execution-accuracy scoring: run SQL against a seeded database and compare results.

This is the standard evaluation approach used by academic NL2SQL
benchmarks (Spider, BIRD): two queries are "correct" relative to each other
if they return the same result set when run against the same database,
regardless of how differently they're written. That makes it a much fairer
signal than exact text match, at the cost of needing an actual database
instance to run against -- see db_builder.py for how that's built from a
plain schema file.
"""

from __future__ import annotations

import sqlite3
from collections import Counter

import sqlglot
from sqlglot import exp


def _to_select_ast(sql: str, source_dialect: str) -> exp.Expression:
    parsed = sqlglot.parse_one(sql, read=source_dialect or None)
    node = parsed.this if isinstance(parsed, exp.With) else parsed
    if not isinstance(node, (exp.Select, exp.Union)):
        raise ValueError("only SELECT queries can be executed against the seeded database")
    return parsed


def _run_select(conn: sqlite3.Connection, sql: str, source_dialect: str) -> list[tuple]:
    ast = _to_select_ast(sql, source_dialect)
    sqlite_sql = ast.sql(dialect="sqlite")
    cursor = conn.cursor()
    cursor.execute(sqlite_sql)
    return cursor.fetchall()


def _normalize_row(row: tuple) -> tuple:
    """Sort a row's values so column order doesn't affect matching."""
    return tuple(sorted(row, key=lambda v: (v is None, type(v).__name__, str(v))))


def compute_execution_match(
    conn: sqlite3.Connection, reference_sql: str, generated_sql: str, source_dialect: str = ""
) -> dict:
    """Run both queries and compare results as order-independent row multisets.

    Never raises -- a failing query just counts as not executable/matching.
    """
    ref_rows = gen_rows = None
    ref_error = gen_error = None
    try:
        ref_rows = _run_select(conn, reference_sql, source_dialect)
    except Exception as e:
        ref_error = str(e)
    try:
        gen_rows = _run_select(conn, generated_sql, source_dialect)
    except Exception as e:
        gen_error = str(e)

    match = (
        ref_rows is not None
        and gen_rows is not None
        and Counter(map(_normalize_row, ref_rows)) == Counter(map(_normalize_row, gen_rows))
    )

    return {
        "reference_executable": ref_error is None,
        "generated_executable": gen_error is None,
        "execution_match": match,
        "reference_error": ref_error,
        "generated_error": gen_error,
    }


def aggregate_execution(results: list[dict]) -> dict:
    n = len(results)
    if n == 0:
        return {"n": 0, "reference_executable": 0, "generated_executable": 0, "execution_match": 0}
    return {
        "n": n,
        "reference_executable": sum(1 for r in results if r.get("reference_executable")),
        "generated_executable": sum(1 for r in results if r.get("generated_executable")),
        "execution_match": sum(1 for r in results if r.get("execution_match")),
    }
