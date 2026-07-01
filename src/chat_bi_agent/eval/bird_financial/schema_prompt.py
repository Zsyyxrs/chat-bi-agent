"""Build an English schema description block for BIRD's `financial` DB.

The block is injected verbatim into the LLM prompt. It combines:

- ``dev_tables.json``: authoritative table/column/type/PK/FK structure.
- ``database_description/<table>.csv``: per-column human descriptions + value enums
  (crucial for BIRD — enum values like ``'POPLATEK MESICNE'`` are not derivable from
  the SQLite schema alone).
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class _ColumnDoc:
    description: str
    value_description: str


def _read_description_csv(csv_path: Path) -> dict[str, _ColumnDoc]:
    """Return {original_column_name: _ColumnDoc}. Tolerates UTF-8 BOM."""
    out: dict[str, _ColumnDoc] = {}
    # utf-8-sig strips the BOM prefix that BIRD's CSVs carry.
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("original_column_name") or "").strip()
            if not name:
                continue
            out[name] = _ColumnDoc(
                description=(row.get("column_description") or "").strip(),
                value_description=(row.get("value_description") or "").strip(),
            )
    return out


def build_financial_schema_block(
    tables_json_path: Path,
    description_dir: Path,
) -> str:
    """Assemble a Markdown-ish schema block for the ``financial`` DB.

    Output shape (one section per table, columns in original order)::

        Table: account
          - account_id: integer (PK) -- the id of the account
          - district_id: integer (FK -> district.district_id) -- location of branch
          - frequency: text -- frequency of the account
              Values: 'POPLATEK MESICNE' = monthly; 'POPLATEK TYDNE' = weekly; ...
    """
    tables_data = json.loads(Path(tables_json_path).read_text(encoding="utf-8"))
    fin = next(x for x in tables_data if x["db_id"] == "financial")

    table_names: list[str] = fin["table_names_original"]
    column_names: list[list] = fin["column_names_original"]  # [[table_idx, col_name], ...]
    column_types: list[str] = fin["column_types"]
    pk_indices: set[int] = set(fin["primary_keys"])
    # fk pairs are (from_col_idx, to_col_idx)
    fk_map: dict[int, tuple[str, str]] = {}
    for from_idx, to_idx in fin["foreign_keys"]:
        to_tbl_idx, to_col_name = column_names[to_idx]
        fk_map[from_idx] = (table_names[to_tbl_idx], to_col_name)

    # Preload descriptions per table
    descs_per_table: dict[str, dict[str, _ColumnDoc]] = {}
    for tbl in table_names:
        csv_path = description_dir / f"{tbl}.csv"
        descs_per_table[tbl] = _read_description_csv(csv_path) if csv_path.exists() else {}

    lines: list[str] = ["Database: financial (SQLite)", ""]
    for tbl_idx, tbl_name in enumerate(table_names):
        lines.append(f"Table: {tbl_name}")
        for col_idx, (owner_tbl_idx, col_name) in enumerate(column_names):
            if owner_tbl_idx != tbl_idx:
                continue
            col_type = column_types[col_idx]
            annotations: list[str] = []
            if col_idx in pk_indices:
                annotations.append("PK")
            if col_idx in fk_map:
                ref_tbl, ref_col = fk_map[col_idx]
                annotations.append(f"FK -> {ref_tbl}.{ref_col}")
            anno_str = f" ({', '.join(annotations)})" if annotations else ""

            doc = descs_per_table.get(tbl_name, {}).get(col_name)
            desc_str = ""
            if doc and doc.description:
                desc_str = f" -- {doc.description}"

            lines.append(f"  - {col_name}: {col_type}{anno_str}{desc_str}")

            if doc and doc.value_description:
                # Collapse newlines so the block stays compact
                vd = " ".join(doc.value_description.split())
                # Truncate ridiculously long value descriptions
                if len(vd) > 300:
                    vd = vd[:297] + "..."
                lines.append(f"      Values: {vd}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
