"""Convert Sterling entity XML files to markdown for RAG ingestion.

Handles three entity XML formats:
  - Product entity XMLs (multi-entity, from <INSTALL>/repository/datatypes/)
  - Custom entity XMLs (single-entity, project-specific tables)
  - Custom views (View="true" entities)

Each entity becomes one markdown file with table definition, columns,
primary key, indices, relationships (Parent, ForeignKey, RelationShip),
and ordering info.

Usage:
    python scripts/convert-entity-xml.py /path/to/entity-xmls/
    python scripts/convert-entity-xml.py /path/to/omp_tables.xml

    # Resolve DataType names to actual DB types using datatypes.xml
    python scripts/convert-entity-xml.py /path/to/xmls/ --datatypes /path/to/datatypes.xml

    # Output goes alongside the source files (or use --output)
    python scripts/convert-entity-xml.py /path/to/xmls/ --output ~/sterling-knowledge/data-model/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from xml.etree import ElementTree as ET


def _load_datatypes(path: Path) -> dict[str, str]:
    """Load datatypes.xml into a name → 'TYPE(SIZE)' lookup map."""
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
    """Resolve a column's type: DataType lookup → Type+Size fallback."""
    dtype_name = attr.get("DataType", "")
    if dtype_name and type_map and dtype_name in type_map:
        return f"{dtype_name} → {type_map[dtype_name]}"
    if dtype_name and type_map:
        return dtype_name
    if dtype_name:
        return dtype_name
    db_type = attr.get("Type", "")
    size = attr.get("Size", "")
    if db_type and size:
        return f"{db_type}({size})"
    return db_type or "unknown"


def _entity_header(entity: ET.Element, table_name: str) -> list[str]:
    description = entity.get("Description", "")
    entity_type = entity.get("EntityType", entity.get("TableType", ""))
    prefix = entity.get("Prefix", "")
    xml_name = entity.get("XMLName", "")
    is_view = entity.get("View", "").lower() == "true"
    module = entity.get("Module", "")
    has_history = entity.get("HasHistory", "")
    cacheable = entity.get("Cacheable", "")

    lines = [f"# {table_name}", ""]
    if description:
        lines += [description, ""]

    meta = [f"**Type:** {'View' if is_view else 'Table'}"]
    if entity_type:
        meta.append(f"**Category:** {entity_type}")
    if module:
        meta.append(f"**Module:** {module}")
    if prefix:
        meta.append(f"**Prefix:** {prefix}")
    if xml_name:
        meta.append(f"**XML Name:** {xml_name}")
    if has_history == "Y":
        meta.append("**Has History:** Yes")
    if cacheable == "true":
        meta.append("**Cacheable:** Yes")
    lines += [" | ".join(meta), ""]
    return lines


def _entity_columns(entity: ET.Element, type_map: dict[str, str]) -> list[str]:
    attrs = entity.findall(".//Attributes/Attribute")
    if not attrs:
        return []
    lines = [
        "## Columns",
        "",
        "| Column | Data Type | Nullable | Description |",
        "| --- | --- | --- | --- |",
    ]
    for attr in attrs:
        col = attr.get("ColumnName", "")
        dtype = _resolve_column_type(attr, type_map)
        nullable = attr.get("Nullable", "")
        desc = attr.get("Description", "").replace("|", "\\|")
        default = attr.get("DefaultValue", "").strip()
        if default and default not in ("' '", "' ' ", "&apos; &apos;"):
            desc += f" (default: {default})"
        lines.append(f"| {col} | {dtype} | {nullable} | {desc} |")
    lines.append("")
    return lines


def _entity_keys_and_indices(entity: ET.Element) -> list[str]:
    lines: list[str] = []
    pk = entity.find("PrimaryKey")
    if pk is not None:
        pk_name = pk.get("Name", "")
        pk_cols = [a.get("ColumnName", "") for a in pk.findall("Attribute")]
        lines += [f"## Primary Key: {pk_name}", "", ", ".join(pk_cols), ""]

    indices = entity.findall(".//Indices/Index")
    if indices:
        lines += ["## Indices", "", "| Index | Columns | Unique |", "| --- | --- | --- |"]
        for idx in indices:
            idx_name = idx.get("Name", "")
            unique = "Yes" if idx.get("Unique", "").lower() == "true" else ""
            cols = [c.get("Name", "") for c in idx.findall("Column")]
            lines.append(f"| {idx_name} | {', '.join(cols)} | {unique} |")
        lines.append("")
    return lines


def _entity_relationships(entity: ET.Element) -> list[str]:
    lines: list[str] = []

    parent = entity.find("Parent")
    if parent is not None:
        parent_table = parent.get("ParentTableName", "")
        lines += ["## Parent Relationship", "", f"**Parent Table:** {parent_table}", ""]
        for a in parent.findall("Attribute"):
            child_col = a.get("ColumnName", "")
            parent_col = a.get("ParentColumnName", "")
            lines.append(f"- {child_col} → {parent_table}.{parent_col}")
        lines.append("")

    fks = entity.findall(".//ForeignKeys/ForeignKey")
    if fks:
        lines += ["## Foreign Keys", ""]
        for fk in fks:
            fk_parent = fk.get("ParentTableName", "")
            fk_name = fk.get("XMLName", "")
            joins = []
            for a in fk.findall("Attribute"):
                joins.append(
                    f"{a.get('ColumnName', '')} → {fk_parent}.{a.get('ParentColumnName', '')}"
                )
            lines.append(f"- **{fk_name}** → {fk_parent}: {', '.join(joins)}")
        lines.append("")

    rels = entity.findall(".//RelationShips/RelationShip")
    if rels:
        lines += [
            "## Relationships",
            "",
            "| Name | Foreign Entity | Cardinality | Type | Join |",
            "| --- | --- | --- | --- | --- |",
        ]
        for rel in rels:
            joins = []
            for a in rel.findall("Attribute"):
                joins.append(f"{a.get('Name', '')} = {a.get('ParentName', '')}")
            lines.append(
                f"| {rel.get('Name', '')} | {rel.get('ForeignEntity', '')} "
                f"| {rel.get('Cardinality', '')} | {rel.get('Type', '')} "
                f"| {', '.join(joins)} |"
            )
        lines.append("")

    order_by = entity.find("OrderBy")
    if order_by is not None:
        lines += [f"**Default Order By:** {order_by.get('Value', '')}", ""]

    return lines


def _resolve_table_name(entity: ET.Element) -> str:
    table_name = entity.get("TableName", "")
    if not table_name:
        prefix = entity.get("Prefix", "")
        name = entity.get("Name", "UNKNOWN")
        table_name = f"{prefix}{name}".upper()
    return table_name


def _convert_entity(entity: ET.Element, source_file: str, type_map: dict[str, str]) -> str:
    table_name = _resolve_table_name(entity)
    lines = _entity_header(entity, table_name)
    lines += _entity_columns(entity, type_map)
    lines += _entity_keys_and_indices(entity)
    lines += _entity_relationships(entity)
    lines.append(f"*Source: {source_file}*")
    return "\n".join(lines)


def convert_file(xml_path: Path, output_dir: Path, type_map: dict[str, str]) -> list[Path]:
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as e:
        print(f"  ERROR parsing {xml_path}: {e}")
        return []

    root = tree.getroot()
    entities = root.findall(".//Entity")
    sequences = root.findall(".//Sequence")
    if not entities and not sequences:
        return []

    created: list[Path] = []
    for entity in entities:
        table_name = _resolve_table_name(entity)
        md = _convert_entity(entity, xml_path.name, type_map)
        out_file = output_dir / f"{table_name}.md"
        if out_file.exists():
            existing = out_file.read_text(encoding="utf-8")
            out_file.write_text(
                existing + "\n\n---\n\n" + md,
                encoding="utf-8",
            )
        else:
            out_file.write_text(md, encoding="utf-8")
        created.append(out_file)

    if sequences:
        seq_lines = ["# Database Sequences", "", f"*Source: {xml_path.name}*", ""]
        seq_lines += [
            "| Sequence | Start | Min | Max | Increment | Cache |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for seq in sequences:
            seq_lines.append(
                f"| {seq.get('Name', '')} | {seq.get('Startwith', '')} "
                f"| {seq.get('Minvalue', '')} | {seq.get('Maxvalue', '')} "
                f"| {seq.get('Increment', '')} | {seq.get('Cachesize', '')} |"
            )
        seq_file = output_dir / f"_SEQUENCES_{xml_path.stem}.md"
        if seq_file.exists():
            existing = seq_file.read_text(encoding="utf-8")
            seq_file.write_text(existing + "\n\n---\n\n" + "\n".join(seq_lines), encoding="utf-8")
        else:
            seq_file.write_text("\n".join(seq_lines), encoding="utf-8")
        created.append(seq_file)

    return created


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert Sterling entity XMLs to markdown")
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
        xml_files = list(args.path.rglob("*.xml"))
        if args.datatypes:
            xml_files = [f for f in xml_files if f != args.datatypes.resolve()]
    else:
        print(f"Not found: {args.path}")
        sys.exit(1)

    if not xml_files:
        print(f"No XML files found in {args.path}")
        return

    output_dir = args.output or (args.path.parent if args.path.is_file() else args.path)
    output_dir.mkdir(parents=True, exist_ok=True)

    total = 0
    for xf in xml_files:
        created = convert_file(xf, output_dir, type_map)
        for f in created:
            print(f"  {f.name}")
        total += len(created)

    print(f"\nConverted {len(xml_files)} XML files → {total} entity markdown files")
    print(f"Output: {output_dir}")


if __name__ == "__main__":
    main()
