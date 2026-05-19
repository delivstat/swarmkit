"""Convert Sterling entity XML files to markdown for RAG ingestion.

Processes ALL entity XML files in one shot, merges entities by
TableName, and writes one consolidated markdown file per table.
Each column, index, and relationship is annotated with its source file.

Handles:
  - Product entity XMLs (multi-entity, from <INSTALL>/repository/datatypes/)
  - Custom entity XMLs (single-entity, project-specific tables)
  - Custom views (View="true" entities)
  - Extension XMLs (add columns/indices to existing tables)

Generates:
  - One .md per table (merged across all source files)
  - _RELATIONSHIPS.md (cross-reference of all parent/child/FK links)
  - _SEQUENCES.md (all sequences across all files)

Usage:
    # Process all entity XMLs in one shot
    python scripts/convert-entity-xml.py /path/to/all-xmls/ \\
      --datatypes /path/to/datatypes.xml \\
      --output ~/sterling-knowledge/data-model/
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET


@dataclass
class ColumnDef:
    name: str
    data_type: str
    nullable: str
    description: str
    default: str
    source: str


@dataclass
class IndexDef:
    name: str
    columns: list[str]
    unique: bool
    source: str


@dataclass
class RelDef:
    kind: str  # "parent", "foreign_key", "relationship"
    name: str
    parent_table: str
    cardinality: str
    rel_type: str
    joins: list[tuple[str, str]]
    source: str


@dataclass
class SeqDef:
    name: str
    start: str
    min_val: str
    max_val: str
    increment: str
    cache: str
    source: str


@dataclass
class MergedEntity:
    table_name: str
    description: str = ""
    entity_type: str = ""
    prefix: str = ""
    xml_name: str = ""
    is_view: bool = False
    module: str = ""
    has_history: bool = False
    cacheable: bool = False
    pk_name: str = ""
    pk_columns: list[str] = field(default_factory=list)
    order_by: str = ""
    columns: list[ColumnDef] = field(default_factory=list)
    indices: list[IndexDef] = field(default_factory=list)
    relationships: list[RelDef] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)


def _load_datatypes(path: Path) -> dict[str, str]:
    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        print(f"  WARNING: failed to parse datatypes.xml: {e}")
        return {}
    type_map: dict[str, str] = {}
    for dt in tree.getroot().findall("DataType"):
        name = dt.get("Name", "")
        if not name:
            continue
        db_type = dt.get("Type", "")
        size = dt.get("Size", "")
        decimals = dt.get("DecimalDigits", "")
        if decimals:
            type_map[name] = f"{db_type}({size},{decimals})"
        elif size:
            type_map[name] = f"{db_type}({size})"
        else:
            type_map[name] = db_type
    return type_map


def _resolve_column_type(attr: ET.Element, type_map: dict[str, str]) -> str:
    dtype_name = attr.get("DataType", "")
    if dtype_name and type_map and dtype_name in type_map:
        return f"{dtype_name} → {type_map[dtype_name]}"
    if dtype_name:
        return dtype_name
    db_type = attr.get("Type", "")
    size = attr.get("Size", "")
    if db_type and size:
        return f"{db_type}({size})"
    return db_type or "unknown"


def _resolve_table_name(entity: ET.Element) -> str:
    table_name = entity.get("TableName", "")
    if not table_name:
        prefix = entity.get("Prefix", "")
        name = entity.get("Name", "UNKNOWN")
        table_name = f"{prefix}{name}".upper()
    return table_name


def _parse_entity(
    entity: ET.Element, source_file: str, type_map: dict[str, str]
) -> tuple[str, MergedEntity, list[SeqDef]]:
    table_name = _resolve_table_name(entity)

    me = MergedEntity(table_name=table_name)
    me.description = entity.get("Description", "")
    me.entity_type = entity.get("EntityType", entity.get("TableType", ""))
    me.prefix = entity.get("Prefix", "")
    me.xml_name = entity.get("XMLName", "")
    me.is_view = entity.get("View", "").lower() == "true"
    me.module = entity.get("Module", "")
    me.has_history = entity.get("HasHistory", "") == "Y"
    me.cacheable = entity.get("Cacheable", "") == "true"
    me.sources = [source_file]

    for attr in entity.findall(".//Attributes/Attribute"):
        col_name = attr.get("ColumnName", "")
        default = attr.get("DefaultValue", "").strip()
        if default in ("' '", "' ' ", "&apos; &apos;"):
            default = ""
        me.columns.append(
            ColumnDef(
                name=col_name,
                data_type=_resolve_column_type(attr, type_map),
                nullable=attr.get("Nullable", ""),
                description=attr.get("Description", "").replace("|", "\\|"),
                default=default,
                source=source_file,
            )
        )

    pk = entity.find("PrimaryKey")
    if pk is not None:
        me.pk_name = pk.get("Name", "")
        me.pk_columns = [a.get("ColumnName", "") for a in pk.findall("Attribute")]

    for idx in entity.findall(".//Indices/Index"):
        me.indices.append(
            IndexDef(
                name=idx.get("Name", ""),
                columns=[c.get("Name", "") for c in idx.findall("Column")],
                unique=idx.get("Unique", "").lower() == "true",
                source=source_file,
            )
        )

    parent = entity.find("Parent")
    if parent is not None:
        parent_table = parent.get("ParentTableName", "")
        joins = [
            (a.get("ColumnName", ""), a.get("ParentColumnName", ""))
            for a in parent.findall("Attribute")
        ]
        me.relationships.append(RelDef("parent", "", parent_table, "", "", joins, source_file))

    for fk in entity.findall(".//ForeignKeys/ForeignKey"):
        fk_parent = fk.get("ParentTableName", "")
        fk_name = fk.get("XMLName", "")
        joins = [
            (a.get("ColumnName", ""), a.get("ParentColumnName", ""))
            for a in fk.findall("Attribute")
        ]
        me.relationships.append(
            RelDef("foreign_key", fk_name, fk_parent, "", "", joins, source_file)
        )

    for rel in entity.findall(".//RelationShips/RelationShip"):
        joins = [(a.get("Name", ""), a.get("ParentName", "")) for a in rel.findall("Attribute")]
        me.relationships.append(
            RelDef(
                "relationship",
                rel.get("Name", ""),
                rel.get("ForeignEntity", ""),
                rel.get("Cardinality", ""),
                rel.get("Type", ""),
                joins,
                source_file,
            )
        )

    order_by = entity.find("OrderBy")
    if order_by is not None:
        me.order_by = order_by.get("Value", "")

    return table_name, me, []


def _merge_entity(target: MergedEntity, source: MergedEntity) -> None:  # noqa: PLR0912
    if not target.description and source.description:
        target.description = source.description
    if not target.entity_type and source.entity_type:
        target.entity_type = source.entity_type
    if not target.prefix and source.prefix:
        target.prefix = source.prefix
    if not target.xml_name and source.xml_name:
        target.xml_name = source.xml_name
    if source.is_view:
        target.is_view = True
    if not target.module and source.module:
        target.module = source.module
    if source.has_history:
        target.has_history = True
    if source.cacheable:
        target.cacheable = True
    if not target.pk_name and source.pk_name:
        target.pk_name = source.pk_name
        target.pk_columns = source.pk_columns
    if not target.order_by and source.order_by:
        target.order_by = source.order_by

    existing_cols = {c.name for c in target.columns}
    for col in source.columns:
        if col.name not in existing_cols:
            target.columns.append(col)
            existing_cols.add(col.name)

    existing_idx = {i.name for i in target.indices}
    for idx in source.indices:
        if idx.name not in existing_idx:
            target.indices.append(idx)
            existing_idx.add(idx.name)

    target.relationships.extend(source.relationships)

    if source.sources[0] not in target.sources:
        target.sources.append(source.sources[0])


def _render_entity(me: MergedEntity) -> str:  # noqa: PLR0912, PLR0915
    lines = [f"# {me.table_name}", ""]
    if me.description:
        lines += [me.description, ""]

    meta = [f"**Type:** {'View' if me.is_view else 'Table'}"]
    if me.entity_type:
        meta.append(f"**Category:** {me.entity_type}")
    if me.module:
        meta.append(f"**Module:** {me.module}")
    if me.prefix:
        meta.append(f"**Prefix:** {me.prefix}")
    if me.xml_name:
        meta.append(f"**XML Name:** {me.xml_name}")
    if me.has_history:
        meta.append("**Has History:** Yes")
    if me.cacheable:
        meta.append("**Cacheable:** Yes")
    lines += [" | ".join(meta), ""]

    if len(me.sources) > 1:
        lines.append(f"**Sources:** {', '.join(me.sources)}")
        lines.append("")

    if me.columns:
        lines += [
            "## Columns",
            "",
            "| Column | Data Type | Nullable | Source | Description |",
            "| --- | --- | --- | --- | --- |",
        ]
        for col in me.columns:
            desc = col.description
            if col.default:
                desc += f" (default: {col.default})"
            lines.append(
                f"| {col.name} | {col.data_type} | {col.nullable} | {col.source} | {desc} |"
            )
        lines.append("")

    if me.pk_name:
        lines += [f"## Primary Key: {me.pk_name}", "", ", ".join(me.pk_columns), ""]

    if me.indices:
        lines += [
            "## Indices",
            "",
            "| Index | Columns | Unique | Source |",
            "| --- | --- | --- | --- |",
        ]
        for idx in me.indices:
            unique = "Yes" if idx.unique else ""
            lines.append(f"| {idx.name} | {', '.join(idx.columns)} | {unique} | {idx.source} |")
        lines.append("")

    parents = [r for r in me.relationships if r.kind == "parent"]
    fks = [r for r in me.relationships if r.kind == "foreign_key"]
    rels = [r for r in me.relationships if r.kind == "relationship"]

    if parents:
        lines += ["## Parent Relationships", ""]
        for p in parents:
            join_str = ", ".join(f"{c} → {me.table_name}.{pc}" for c, pc in p.joins)
            lines.append(f"- **{p.parent_table}**: {join_str} *(from {p.source})*")
        lines.append("")

    if fks:
        lines += ["## Foreign Keys", ""]
        for fk in fks:
            join_str = ", ".join(f"{c} → {fk.parent_table}.{pc}" for c, pc in fk.joins)
            lines.append(f"- **{fk.name}** → {fk.parent_table}: {join_str} *(from {fk.source})*")
        lines.append("")

    if rels:
        lines += [
            "## Relationships",
            "",
            "| Name | Foreign Entity | Cardinality | Type | Join | Source |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for rel in rels:
            join_str = ", ".join(f"{c} = {pc}" for c, pc in rel.joins)
            lines.append(
                f"| {rel.name} | {rel.parent_table} | {rel.cardinality} "
                f"| {rel.rel_type} | {join_str} | {rel.source} |"
            )
        lines.append("")

    if me.order_by:
        lines += [f"**Default Order By:** {me.order_by}", ""]

    lines.append(f"*Sources: {', '.join(me.sources)}*")
    return "\n".join(lines)


def _render_relationships(entities: dict[str, MergedEntity]) -> str:
    lines = [
        "# Data Model — All Relationships",
        "",
        "Cross-reference of all parent/child, foreign key, and entity "
        "relationships across the Sterling data model.",
        "",
        "| Child Table | Relationship | Parent Table/Entity | Join | Type | Source |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for table_name in sorted(entities):
        me = entities[table_name]
        for rel in me.relationships:
            join_str = ", ".join(f"{c} = {pc}" for c, pc in rel.joins)
            lines.append(
                f"| {table_name} | {rel.kind} | {rel.parent_table} "
                f"| {join_str} | {rel.cardinality or rel.rel_type or '-'} "
                f"| {rel.source} |"
            )
    lines.append("")
    return "\n".join(lines)


def _render_sequences(sequences: list[SeqDef]) -> str:
    lines = [
        "# Database Sequences",
        "",
        "| Sequence | Start | Min | Max | Increment | Cache | Source |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for seq in sequences:
        lines.append(
            f"| {seq.name} | {seq.start} | {seq.min_val} | {seq.max_val} "
            f"| {seq.increment} | {seq.cache} | {seq.source} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:  # noqa: PLR0912, PLR0915
    parser = argparse.ArgumentParser(
        description="Convert Sterling entity XMLs to consolidated markdown"
    )
    parser.add_argument("path", type=Path, help="Entity XML file or directory containing them")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output directory (default: alongside source files)",
    )
    parser.add_argument(
        "--datatypes",
        type=Path,
        default=None,
        help="Path to datatypes.xml — resolves DataType names to actual DB types",
    )
    args = parser.parse_args()

    type_map: dict[str, str] = {}
    if args.datatypes:
        type_map = _load_datatypes(args.datatypes)
        print(f"Loaded {len(type_map)} data type definitions from {args.datatypes}")

    if args.path.is_file():
        xml_files = [args.path]
    elif args.path.is_dir():
        xml_files = sorted(args.path.rglob("*.xml"))
        if args.datatypes:
            xml_files = [f for f in xml_files if f != args.datatypes.resolve()]
    else:
        print(f"Not found: {args.path}")
        sys.exit(1)

    if not xml_files:
        print(f"No XML files found in {args.path}")
        return

    # Phase 1: Parse all files, merge entities by TableName
    print(f"Parsing {len(xml_files)} XML files...")
    entities: dict[str, MergedEntity] = {}
    all_sequences: list[SeqDef] = []

    for xf in xml_files:
        try:
            tree = ET.parse(xf)
        except ET.ParseError as e:
            print(f"  ERROR parsing {xf}: {e}")
            continue

        root = tree.getroot()
        source_name = xf.name

        for entity_el in root.findall(".//Entity"):
            table_name, parsed, _ = _parse_entity(entity_el, source_name, type_map)
            if table_name in entities:
                _merge_entity(entities[table_name], parsed)
            else:
                entities[table_name] = parsed

        for seq in root.findall(".//Sequence"):
            all_sequences.append(
                SeqDef(
                    name=seq.get("Name", ""),
                    start=seq.get("Startwith", ""),
                    min_val=seq.get("Minvalue", ""),
                    max_val=seq.get("Maxvalue", ""),
                    increment=seq.get("Increment", ""),
                    cache=seq.get("Cachesize", ""),
                    source=xf.name,
                )
            )

    print(f"  {len(entities)} tables, {len(all_sequences)} sequences")

    multi_source = [t for t, e in entities.items() if len(e.sources) > 1]
    if multi_source:
        print(f"  {len(multi_source)} tables merged from multiple files:")
        for t in sorted(multi_source):
            print(f"    {t} ← {', '.join(entities[t].sources)}")

    # Phase 2: Write consolidated output
    output_dir = args.output or (args.path.parent if args.path.is_file() else args.path)
    output_dir.mkdir(parents=True, exist_ok=True)

    for table_name in sorted(entities):
        md = _render_entity(entities[table_name])
        out_file = output_dir / f"{table_name}.md"
        out_file.write_text(md, encoding="utf-8")
        print(f"  {out_file.name}")

    rel_md = _render_relationships(entities)
    rel_file = output_dir / "_RELATIONSHIPS.md"
    rel_file.write_text(rel_md, encoding="utf-8")
    print(f"  {rel_file.name}")

    if all_sequences:
        seq_md = _render_sequences(all_sequences)
        seq_file = output_dir / "_SEQUENCES.md"
        seq_file.write_text(seq_md, encoding="utf-8")
        print(f"  {seq_file.name}")

    total = len(entities) + 1 + (1 if all_sequences else 0)
    print(f"\nGenerated {total} files from {len(xml_files)} XML sources")
    print(f"Output: {output_dir}")


if __name__ == "__main__":
    main()
