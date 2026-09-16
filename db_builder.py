"""Build a seeded SQLite database from a schema written in any SQL dialect.

Used to score NL2SQL outputs by execution accuracy: run the generated and
reference queries against real (synthetic) data and compare result sets,
instead of comparing query text. SQLite is the execution target regardless
of your schema's original dialect -- it's embedded, needs no server, and
sqlglot can transpile most standard DDL/DML into it.
"""

from __future__ import annotations

import random
import re
import sqlite3
import string
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta

import sqlglot
from sqlglot import exp

_CREATE_TABLE_RE = re.compile(r"(?is)^create\s+table\b")


def transpile_to_sqlite(sql: str, source_dialect: str) -> str:
    """Convert one or more ;-separated statements from `source_dialect` to SQLite syntax."""
    statements = sqlglot.transpile(sql, read=source_dialect or None, write="sqlite")
    return ";\n".join(statements)


def _extract_create_table_asts(schema_sql: str, source_dialect: str) -> list[exp.Create]:
    """Pull out just the CREATE TABLE statements from a schema file.

    Real-world schema dumps often carry more than table definitions --
    stored procedures/functions, `DELIMITER` blocks, views, comments -- and
    those routinely use syntax sqlglot (or any single generic SQL parser)
    can't fully parse. Rather than fail the whole build on the first
    unparseable statement, split on top-level `;` first and only attempt to
    parse chunks that look like a CREATE TABLE. A chunk that still fails to
    parse (unusual column syntax, etc.) is skipped with a warning instead of
    aborting the whole schema.
    """
    tables = []
    for chunk in schema_sql.split(";"):
        chunk = chunk.strip()
        if not _CREATE_TABLE_RE.match(chunk):
            continue
        try:
            stmt = sqlglot.parse_one(chunk, read=source_dialect or None)
        except Exception as e:
            print(f"Warning: skipping unparseable CREATE TABLE statement ({e})", file=sys.stderr)
            continue
        if isinstance(stmt, exp.Create) and isinstance(stmt.this, exp.Schema):
            tables.append(stmt)
    return tables


@dataclass
class TableSpec:
    name: str
    columns: list[tuple[str, str]] = field(default_factory=list)  # (name, type)
    primary_key: str | None = None
    foreign_keys: dict[str, tuple[str, str]] = field(default_factory=dict)  # local col -> (ref table, ref col)
    allowed_values: dict[str, list] = field(default_factory=dict)  # col -> declared value set (ENUM / CHECK IN)


def _reference_target(ref_node: exp.Reference) -> tuple[str, str]:
    schema = ref_node.this
    table = schema.this.this.name
    col = schema.expressions[0].name
    return table, col


def _literal_values(expressions: list[exp.Expression]) -> list:
    return [e.this for e in expressions if isinstance(e, exp.Literal)]


def _check_in_values(check: exp.CheckColumnConstraint) -> tuple[str, list] | None:
    """If `check` is a `CHECK (col IN (...))` constraint (column-level or
    table-level -- both parse to the same node), return (col_name, values)."""
    condition = check.this
    if not isinstance(condition, exp.In):
        return None
    target = condition.this
    if not isinstance(target, exp.Column):
        return None
    values = _literal_values(condition.expressions)
    return (target.name, values) if values else None


def parse_schema(schema_sql: str, source_dialect: str) -> list[TableSpec]:
    """Extract table/column/primary-key/foreign-key structure from CREATE TABLE
    statements, in a best-effort dependency order (a table only depends on
    tables earlier in the returned list) so seeding can respect foreign keys.

    Supports single-column primary and foreign keys, inline or as table-level
    constraints. Composite keys aren't specially handled -- only the first
    column of a composite key is treated as "the" key for seeding purposes.
    """
    specs: dict[str, TableSpec] = {}
    for stmt in _extract_create_table_asts(schema_sql, source_dialect):
        table_name = stmt.this.this.name
        spec = TableSpec(name=table_name)

        for col in stmt.find_all(exp.ColumnDef):
            col_name = col.this.name
            data_type = col.args.get("kind")
            col_type = str(data_type or "TEXT")
            spec.columns.append((col_name, col_type))
            if data_type is not None and data_type.this == exp.DataType.Type.ENUM:
                values = _literal_values(data_type.expressions)
                if values:
                    spec.allowed_values[col_name] = values
            for constraint in col.args.get("constraints") or []:
                kind = constraint.kind
                if isinstance(kind, exp.PrimaryKeyColumnConstraint):
                    spec.primary_key = col_name
                elif isinstance(kind, exp.Reference):
                    spec.foreign_keys[col_name] = _reference_target(kind)

        for pk in stmt.find_all(exp.PrimaryKey):
            cols = [c.name for c in pk.args.get("expressions", [])]
            if cols and spec.primary_key is None:
                spec.primary_key = cols[0]

        for fk in stmt.find_all(exp.ForeignKey):
            local_cols = [c.name for c in fk.args.get("expressions", [])]
            ref = fk.args.get("reference")
            if ref and local_cols:
                spec.foreign_keys[local_cols[0]] = _reference_target(ref)

        # CHECK (col IN (...)) constraints -- column-level or table-level,
        # both parse to the same node shape, so one pass covers both.
        for check in stmt.find_all(exp.CheckColumnConstraint):
            result = _check_in_values(check)
            if result:
                col_name, values = result
                spec.allowed_values.setdefault(col_name, values)

        specs[table_name] = spec

    ordered: list[TableSpec] = []
    seen = set()
    remaining = dict(specs)
    while remaining:
        ready = [
            t
            for t in remaining.values()
            if all(ref_table in seen or ref_table not in specs for ref_table, _ in t.foreign_keys.values())
        ]
        if not ready:  # circular or unresolvable dependency -- give up ordering the rest
            ready = list(remaining.values())
        for t in ready:
            ordered.append(t)
            seen.add(t.name)
            del remaining[t.name]
    return ordered


def _render_sqlite_ddl(table: TableSpec) -> str:
    """Synthesize minimal SQLite CREATE TABLE DDL from a TableSpec, rather
    than transpiling the original statement. This sidesteps vendor-specific
    syntax SQLite has no equivalent for (AUTO_INCREMENT placement, storage
    engine/charset properties, inline secondary indexes, etc.) that a real
    production schema dump is likely to contain -- none of it matters here
    since seeding always sets primary keys explicitly.
    """
    def _sqlite_type(col_type: str) -> str:
        # ENUM('a','b',...) isn't valid inside SQLite's column-type grammar
        # (string literals aren't allowed there) -- the values themselves are
        # tracked separately in allowed_values, so the declared type here only
        # needs to give SQLite a sane type affinity.
        return "TEXT" if col_type.upper().startswith("ENUM") else col_type

    col_defs = [
        f'"{name}" {_sqlite_type(col_type)}' + (" PRIMARY KEY" if name == table.primary_key else "")
        for name, col_type in table.columns
    ]
    for local_col, (ref_table, ref_col) in table.foreign_keys.items():
        col_defs.append(f'FOREIGN KEY ("{local_col}") REFERENCES "{ref_table}" ("{ref_col}")')
    return f'CREATE TABLE "{table.name}" (\n  ' + ",\n  ".join(col_defs) + "\n);"


def _random_value(col_type: str, rng: random.Random, allowed_values: list | None = None):
    if allowed_values:
        return rng.choice(allowed_values)
    t = col_type.upper()
    if "INT" in t:
        return rng.randint(1, 10_000)
    if any(k in t for k in ("DEC", "NUMERIC", "FLOAT", "DOUBLE", "REAL")):
        return round(rng.uniform(1, 1000), 2)
    if "BOOL" in t:
        return rng.choice([0, 1])
    if "DATE" in t or "TIME" in t:
        return str(date(2023, 1, 1) + timedelta(days=rng.randint(0, 700)))
    return "".join(rng.choices(string.ascii_lowercase, k=8))


def create_tables(schema_sql: str, source_dialect: str) -> tuple[sqlite3.Connection, list[TableSpec]]:
    """Create an in-memory SQLite database from `schema_sql`, tables only --
    no data. Used directly by callers who want to seed their own rows (see
    `--seed-script` in bench.py) instead of `build_database`'s random fill.
    """
    tables = parse_schema(schema_sql, source_dialect)
    if not tables:
        raise ValueError("No (parseable) CREATE TABLE statements found in schema")

    conn = sqlite3.connect(":memory:")
    created = []
    for table in tables:
        try:
            conn.executescript(_render_sqlite_ddl(table))
            created.append(table)
        except sqlite3.Error as e:
            print(f"Warning: could not create table '{table.name}' in SQLite ({e}) -- skipping", file=sys.stderr)
    if not created:
        raise ValueError("No CREATE TABLE statement could be translated to SQLite")
    conn.commit()
    return conn, created


def build_database(schema_sql: str, source_dialect: str, seed_rows: int = 20, seed: int = 0) -> sqlite3.Connection:
    """Create an in-memory SQLite database from `schema_sql` and fill each
    table with `seed_rows` rows of random data, respecting declared primary
    keys (sequential, unique) and foreign keys (sampled from the referenced
    table's already-generated keys, so joins actually return matching rows).

    Columns declared as `ENUM(...)` or with a `CHECK (col IN (...))`
    constraint are sampled from that declared value set instead of random
    noise, so e.g. `WHERE status = 'COMPLETED'` has a real chance of
    matching. Everything else is type-appropriate random noise with no
    awareness of your data's actual distribution -- for anything beyond
    that (a specific known ID, a realistic date range), use `--seed-script`
    (see bench.py) or call `create_tables()` and seed it yourself.
    """
    rng = random.Random(seed)
    conn, tables = create_tables(schema_sql, source_dialect)

    pk_pools: dict[str, list] = {}
    for table in tables:
        pk_values = []
        for row_index in range(1, seed_rows + 1):
            values = {}
            for col_name, col_type in table.columns:
                if col_name == table.primary_key:
                    values[col_name] = row_index
                elif col_name in table.foreign_keys:
                    ref_table, _ref_col = table.foreign_keys[col_name]
                    pool = pk_pools.get(ref_table)
                    values[col_name] = rng.choice(pool) if pool else row_index
                else:
                    values[col_name] = _random_value(col_type, rng, table.allowed_values.get(col_name))
            cols = list(values.keys())
            placeholders = ", ".join("?" for _ in cols)
            conn.execute(
                f'INSERT INTO "{table.name}" ({", ".join(cols)}) VALUES ({placeholders})',
                [values[c] for c in cols],
            )
            if table.primary_key:
                pk_values.append(values[table.primary_key])
        pk_pools[table.name] = pk_values or list(range(1, seed_rows + 1))

    conn.commit()
    return conn
