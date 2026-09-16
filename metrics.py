"""SQL correctness metrics for comparing model output against a reference query.

Metrics are dialect-aware (via sqlglot) but otherwise schema-agnostic: they
don't assume anything about your table or column names.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp as sqlglot_exp


def extract_sql(text: str) -> str:
    """Strip markdown fences / reasoning prose so scoring targets the SQL, not the wrapper.

    Some models -- especially "thinking" or reasoning-tuned checkpoints --
    answer with a step-by-step preamble before the SQL, even when instructed
    to return only the query. This pulls the SQL out of that.
    """
    text = text.strip()
    if "```" in text:
        for part in text.split("```"):
            part = part.strip()
            if not part:
                continue
            if part.lower().startswith("sql"):
                part = part[3:].strip()
            return part
    lowered = text.lower()
    for keyword in ("select", "with"):
        idx = lowered.find(keyword)
        if idx > 0:
            return text[idx:].strip()
    return text


def sql_is_valid(sql: str, dialect: str) -> bool:
    try:
        sqlglot.parse_one(sql, dialect=dialect)
        return True
    except Exception:
        return False


def sql_tables(sql: str, dialect: str) -> set[str] | None:
    try:
        parsed = sqlglot.parse_one(sql, dialect=dialect)
        return {t.name.lower() for t in parsed.find_all(sqlglot_exp.Table)}
    except Exception:
        return None


def sql_columns(sql: str, dialect: str) -> set[str] | None:
    try:
        parsed = sqlglot.parse_one(sql, dialect=dialect)
        select = parsed if isinstance(parsed, sqlglot_exp.Select) else parsed.find(sqlglot_exp.Select)
        if select is None:
            return None
        return {c.alias_or_name.lower() for c in select.expressions if c.alias_or_name}
    except Exception:
        return None


def compute_metrics(reference_sql: str, generated_sql: str, dialect: str = "") -> dict:
    """Score one generated query against its reference.

    - valid: does the generated SQL parse at all under `dialect`?
    - table_match: same set of tables referenced (order-independent)?
    - col_overlap: fraction of the reference's selected columns also present
      in the generated query's SELECT list (0.0 if either side fails to parse).
    - exact_match: byte-for-byte match after stripping surrounding whitespace.
      This is strict -- logically equivalent SQL with different formatting,
      column order, or aliasing counts as a miss. Treat it as a lower bound,
      not the whole picture; use the per-example detail in the report for
      manual review of near-misses.
    """
    valid = sql_is_valid(generated_sql, dialect)
    ref_tables, gen_tables = sql_tables(reference_sql, dialect), sql_tables(generated_sql, dialect)
    table_match = bool(ref_tables and gen_tables and ref_tables == gen_tables)
    ref_cols, gen_cols = sql_columns(reference_sql, dialect), sql_columns(generated_sql, dialect)
    col_overlap = (len(ref_cols & gen_cols) / len(ref_cols)) if (ref_cols and gen_cols and ref_cols) else 0.0
    exact_match = reference_sql.strip() == generated_sql.strip()
    return {
        "valid": valid,
        "table_match": table_match,
        "col_overlap": col_overlap,
        "exact_match": exact_match,
    }


def aggregate(metrics_list: list[dict]) -> dict:
    n = len(metrics_list)
    if n == 0:
        return {"n": 0, "valid": 0, "table_match": 0, "col_overlap_avg": 0.0, "exact_match": 0}
    return {
        "n": n,
        "valid": sum(m["valid"] for m in metrics_list),
        "table_match": sum(m["table_match"] for m in metrics_list),
        "col_overlap_avg": sum(m["col_overlap"] for m in metrics_list) / n,
        "exact_match": sum(m["exact_match"] for m in metrics_list),
    }
