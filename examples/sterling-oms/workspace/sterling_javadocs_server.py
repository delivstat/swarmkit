# /// script
# dependencies = ["mcp[cli]>=1.0"]
# ///
"""Sterling API Javadocs MCP server — structured access to API documentation.

Parses the Sterling API Javadoc HTML files at startup, builds an
in-memory index, and exposes tools for querying API details, input/output
XML structures, user exits, events, and sample documents.

Usage:
    export STERLING_JAVADOCS_DIR=~/javadocs_v10/api_javadocs
    uv run sterling_javadocs_server.py
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from mcp.server.fastmcp import FastMCP

server = FastMCP("sterling-api-javadocs")

_BASE = Path(
    os.environ.get(
        "STERLING_JAVADOCS_DIR",
        os.path.expanduser("~/javadocs_v10/api_javadocs"),
    )
)


@dataclass
class ApiInfo:
    name: str
    module: str
    prefix: str
    package: str
    description: str = ""
    signature: str = ""
    user_exits: list[dict[str, str]] = field(default_factory=list)
    events: str = ""
    audits: str = ""
    preconditions: str = ""
    postconditions: str = ""
    output_template_support: str = ""
    html_path: str = ""
    input_xsd_html: str = ""
    output_xsd_html: str = ""
    input_dtd: str = ""
    output_dtd: str = ""
    input_xml: str = ""
    output_xml: str = ""
    input_json: str = ""
    output_json: str = ""


_apis: dict[str, ApiInfo] = {}
_apis_by_module: dict[str, list[str]] = {}


def _strip_tags(html: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<p\s*/?>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_section(html: str, label: str) -> str:
    pattern = rf"<b>\s*{re.escape(label)}\s*:?\s*</[bB]>\s*(.*?)(?=<[bB]>|<p\s*class|</div>)"
    m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
    if m:
        return _strip_tags(m.group(1)).strip()
    return ""


def _extract_user_exits(html: str) -> list[dict[str, str]]:
    ue_section = _extract_section(html, "User Exits")
    if not ue_section or ue_section.lower() == "none":
        return []

    exits: list[dict[str, str]] = []
    ue_pattern = r'href="[^"]*?/ue/(\w+)\.html"[^>]*>([^<]+)</a>'
    for m in re.finditer(ue_pattern, html, re.IGNORECASE):
        ue_class = m.group(2).strip()
        desc_after = html[m.end() : m.end() + 500]
        desc_text = _strip_tags(desc_after).strip()
        desc_text = desc_text.split("\n")[0][:200] if desc_text else ""
        exits.append({"class": ue_class, "description": desc_text})

    if not exits and ue_section and ue_section.lower() != "none":
        exits.append({"class": ue_section, "description": ""})

    return exits


def _parse_api_page(html_path: Path, module: str, prefix: str) -> ApiInfo | None:
    api_name = html_path.stem
    try:
        html = html_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    detail_section = re.search(
        r"METHOD DETAIL.*?<div\s+class=\"block\">(.*?)</div>", html, re.DOTALL
    )
    if detail_section:
        description = _strip_tags(detail_section.group(1))
    else:
        brief = re.search(r'<div class="block">(.*?)</div>', html, re.DOTALL)
        description = _strip_tags(brief.group(1)) if brief else ""

    sig_match = re.search(
        r"<h4>" + re.escape(api_name) + r"</h4>\s*<pre>(.*?)</pre>",
        html,
        re.DOTALL,
    )
    signature = _strip_tags(sig_match.group(1)) if sig_match else ""

    user_exits = _extract_user_exits(html)
    events = _extract_section(html, "Events Raised")
    audits = _extract_section(html, "Audits")
    preconditions = _extract_section(html, "Pre-conditions")
    postconditions = _extract_section(html, "Post-conditions")
    output_template = _extract_section(html, "Output Template Support")

    file_prefix = f"{prefix}_{api_name}"

    info = ApiInfo(
        name=api_name,
        module=module,
        prefix=prefix,
        package=f"com.yantra.api.{module}",
        description=description,
        signature=signature,
        user_exits=user_exits,
        events=events or "None",
        audits=audits or "None",
        preconditions=preconditions or "None",
        postconditions=postconditions or "None",
        output_template_support=output_template or "Unknown",
        html_path=str(html_path.relative_to(_BASE)),
    )

    for attr, pattern in [
        ("input_xsd_html", f"XSD/HTML/{file_prefix}_input.html"),
        ("output_xsd_html", f"XSD/HTML/{file_prefix}_output.html"),
        ("input_dtd", f"dtd/{file_prefix}_input_dtd.txt"),
        ("output_dtd", f"dtd/{file_prefix}_output_dtd.txt"),
        ("input_xml", f"XML/{file_prefix}_input.xml"),
        ("output_xml", f"XML/{file_prefix}_output.xml"),
        ("input_json", f"JSON/{file_prefix}_input.json"),
        ("output_json", f"JSON/{file_prefix}_output.json"),
    ]:
        if (_BASE / pattern).exists():
            setattr(info, attr, pattern)

    return info


def _parse_xsd_html(path: Path) -> str:
    """Parse an XSD/HTML input/output description page into readable text."""
    if not path.exists():
        return "File not found."
    html = path.read_text(encoding="utf-8", errors="replace")

    elements: list[str] = []
    current_element = ""

    for row_match in re.finditer(
        r'<A[^>]*name="(\w+)"[^>]*><b>(\w+)</b></A>',
        html,
        re.IGNORECASE,
    ):
        current_element = row_match.group(2)
        elements.append(f"\n## {current_element}\n")

    rows = re.findall(
        r"<tr[^>]*>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*"
        r"<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>",
        html,
        re.DOTALL,
    )
    if rows:
        elements.append("| Attribute | Type | Required | Table.Column |")
        elements.append("| --- | --- | --- | --- |")
        for raw_name, raw_type, raw_req, raw_col in rows:
            col_name = _strip_tags(raw_name).strip()
            col_type = _strip_tags(raw_type).strip()
            col_req = _strip_tags(raw_req).strip()
            col_tbl = _strip_tags(raw_col).strip()
            if col_name and col_name != "Attribute":
                elements.append(f"| {col_name} | {col_type} | {col_req} | {col_tbl} |")

    descs = re.findall(
        r"<xsd:documentation[^>]*>(.*?)</xsd:documentation>",
        html,
        re.DOTALL,
    )
    for _i, desc in enumerate(descs):
        desc_text = _strip_tags(desc).strip()
        if desc_text:
            elements.append(f"  *{desc_text}*")

    return "\n".join(elements) if elements else _strip_tags(html)


def _build_index() -> None:  # noqa: PLR0912
    """Scan the API javadocs directory and build the in-memory index."""
    api_dir = _BASE / "com" / "yantra" / "api"
    if not api_dir.is_dir():
        return

    for module_dir in sorted(api_dir.iterdir()):
        if not module_dir.is_dir():
            continue
        module = module_dir.name
        prefix = module.upper()

        for html_file in sorted(module_dir.rglob("*.html")):
            if html_file.name.startswith("package-"):
                continue

            info = _parse_api_page(html_file, module, prefix)
            if info:
                _apis[info.name] = info
                _apis_by_module.setdefault(module, []).append(info.name)

    # Also scan sub-modules under api (e.g. omp/order, wms/picking)
    for module_dir in sorted(api_dir.iterdir()):
        if not module_dir.is_dir():
            continue
        for sub_dir in sorted(module_dir.iterdir()):
            if not sub_dir.is_dir() or sub_dir.name == "japi":
                continue
            module = module_dir.name
            prefix = module.upper()
            for html_file in sorted(sub_dir.rglob("*.html")):
                if html_file.name.startswith("package-"):
                    continue
                if html_file.stem in _apis:
                    continue
                info = _parse_api_page(html_file, module, prefix)
                if info:
                    info.package = f"com.yantra.api.{module}.{sub_dir.name}"
                    _apis[info.name] = info
                    _apis_by_module.setdefault(module, []).append(info.name)


_build_index()


@server.tool()
def search_apis(query: str) -> str:
    """Search Sterling APIs by keyword. Returns matching API names with descriptions."""
    query_lower = query.lower()
    matches: list[str] = []
    for name, info in sorted(_apis.items()):
        if (
            query_lower in name.lower()
            or query_lower in info.description.lower()
            or query_lower in info.module.lower()
        ):
            desc = (
                info.description[:150] + "..." if len(info.description) > 150 else info.description
            )
            matches.append(f"- **{name}** ({info.package}): {desc}")

    if not matches:
        return f"No APIs found matching '{query}'. Try a broader search term."
    return f"Found {len(matches)} APIs:\n\n" + "\n".join(matches[:30])


@server.tool()
def list_apis(module: str = "") -> str:
    """List all Sterling APIs, optionally filtered by module (yfs, ycd, ycm, etc.)."""
    if module:
        module_lower = module.lower()
        apis = _apis_by_module.get(module_lower, [])
        if not apis:
            available = sorted(_apis_by_module.keys())
            return f"Module '{module}' not found. Available: {', '.join(available)}"
        return f"APIs in {module} ({len(apis)}):\n\n" + "\n".join(
            f"- {name}" for name in sorted(apis)
        )

    lines = [f"Total: {len(_apis)} APIs across {len(_apis_by_module)} modules\n"]
    for mod in sorted(_apis_by_module):
        count = len(_apis_by_module[mod])
        lines.append(f"- **{mod}** ({count} APIs)")
    return "\n".join(lines)


@server.tool()
def get_api_details(api_name: str) -> str:
    """Get full details for a Sterling API: description, signature, user exits, events, etc."""
    info = _apis.get(api_name)
    if not info:
        close = [n for n in _apis if api_name.lower() in n.lower()]
        if close:
            return f"API '{api_name}' not found. Did you mean: {', '.join(close[:5])}?"
        return f"API '{api_name}' not found. Use search_apis to find it."

    lines = [
        f"# {info.name}",
        f"**Package:** {info.package}",
        f"**Module:** {info.module}",
        "",
        info.description,
        "",
        f"**Signature:** `{info.signature}`",
        "",
        f"**User Exits:** {len(info.user_exits)} defined",
    ]
    for ue in info.user_exits:
        lines.append(f"  - `{ue['class']}`: {ue['description']}")

    lines += [
        "",
        f"**Events Raised:** {info.events}",
        f"**Audits:** {info.audits}",
        f"**Pre-conditions:** {info.preconditions}",
        f"**Post-conditions:** {info.postconditions}",
        f"**Output Template Support:** {info.output_template_support}",
        "",
        "**Available documents:**",
    ]
    if info.input_xsd_html:
        lines.append("  - Input XML structure (use get_api_input_xml)")
    if info.output_xsd_html:
        lines.append("  - Output XML structure (use get_api_output_xml)")
    if info.input_xml:
        lines.append("  - Input sample XML + JSON (use get_api_input_sample)")
    if info.output_xml:
        lines.append("  - Output sample XML + JSON (use get_api_output_sample)")
    if info.input_dtd:
        lines.append("  - Input/Output DTDs (use get_api_dtd)")

    return "\n".join(lines)


@server.tool()
def get_api_input_xml(api_name: str) -> str:
    """Get the input XML structure — elements, attributes, types, table.column."""
    info = _apis.get(api_name)
    if not info:
        return f"API '{api_name}' not found."
    if not info.input_xsd_html:
        return f"No input XML documentation found for {api_name}."
    return f"# {api_name} — Input XML\n\n" + _parse_xsd_html(_BASE / info.input_xsd_html)


@server.tool()
def get_api_output_xml(api_name: str) -> str:
    """Get the output XML structure for a Sterling API — elements, attributes, types."""
    info = _apis.get(api_name)
    if not info:
        return f"API '{api_name}' not found."
    if not info.output_xsd_html:
        return f"No output XML documentation found for {api_name}."
    return f"# {api_name} — Output XML\n\n" + _parse_xsd_html(_BASE / info.output_xsd_html)


@server.tool()
def get_api_input_sample(api_name: str) -> str:
    """Get sample input XML and JSON for a Sterling API."""
    info = _apis.get(api_name)
    if not info:
        return f"API '{api_name}' not found."

    parts = [f"# {api_name} — Input Samples\n"]

    if info.input_xml:
        xml_path = _BASE / info.input_xml
        if xml_path.exists():
            parts.append("## XML\n```xml")
            parts.append(xml_path.read_text(encoding="utf-8", errors="replace").strip())
            parts.append("```\n")

    if info.input_json:
        json_path = _BASE / info.input_json
        if json_path.exists():
            parts.append("## JSON\n```json")
            parts.append(json_path.read_text(encoding="utf-8", errors="replace").strip())
            parts.append("```\n")

    return "\n".join(parts) if len(parts) > 1 else f"No input samples found for {api_name}."


@server.tool()
def get_api_output_sample(api_name: str) -> str:
    """Get sample output XML and JSON for a Sterling API."""
    info = _apis.get(api_name)
    if not info:
        return f"API '{api_name}' not found."

    parts = [f"# {api_name} — Output Samples\n"]

    if info.output_xml:
        xml_path = _BASE / info.output_xml
        if xml_path.exists():
            parts.append("## XML\n```xml")
            parts.append(xml_path.read_text(encoding="utf-8", errors="replace").strip())
            parts.append("```\n")

    if info.output_json:
        json_path = _BASE / info.output_json
        if json_path.exists():
            parts.append("## JSON\n```json")
            parts.append(json_path.read_text(encoding="utf-8", errors="replace").strip())
            parts.append("```\n")

    return "\n".join(parts) if len(parts) > 1 else f"No output samples found for {api_name}."


@server.tool()
def get_api_dtd(api_name: str, direction: str = "input") -> str:
    """Get the DTD for a Sterling API input or output. direction: 'input' or 'output'."""
    info = _apis.get(api_name)
    if not info:
        return f"API '{api_name}' not found."

    dtd_attr = "input_dtd" if direction == "input" else "output_dtd"
    dtd_path_str = getattr(info, dtd_attr, "")
    if not dtd_path_str:
        return f"No {direction} DTD found for {api_name}."

    dtd_path = _BASE / dtd_path_str
    if not dtd_path.exists():
        return f"DTD file not found: {dtd_path_str}"

    content = dtd_path.read_text(encoding="utf-8", errors="replace").strip()
    return f"# {api_name} — {direction.title()} DTD\n\n```dtd\n{content}\n```"


@server.tool()
def get_api_user_exits(api_name: str) -> str:
    """Get user exits called by a Sterling API, with descriptions."""
    info = _apis.get(api_name)
    if not info:
        return f"API '{api_name}' not found."

    if not info.user_exits:
        return f"{api_name} does not call any user exits."

    lines = [f"# {api_name} — User Exits\n"]
    for ue in info.user_exits:
        lines.append(f"## {ue['class']}")
        if ue["description"]:
            lines.append(ue["description"])
        lines.append("")

    return "\n".join(lines)


@server.tool()
def get_api_events(api_name: str) -> str:
    """Get events raised by a Sterling API."""
    info = _apis.get(api_name)
    if not info:
        return f"API '{api_name}' not found."

    return f"# {api_name} — Events Raised\n\n{info.events}"


def _export_summaries(output_dir: Path) -> None:
    """Export API summaries as markdown for RAG ingestion.

    Generates one file per module with API names, descriptions,
    user exits, and events — enough for semantic search to answer
    "which API should I use for X?"
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    for module in sorted(_apis_by_module):
        apis = sorted(_apis_by_module[module])
        lines = [
            f"# Sterling APIs — {module.upper()} Module",
            "",
            f"{len(apis)} APIs in the {module} module.",
            "",
        ]
        for api_name in apis:
            info = _apis[api_name]
            lines.append(f"## {info.name}")
            lines.append(f"**Package:** {info.package}")
            lines.append("")
            lines.append(info.description if info.description else "No description.")
            lines.append("")
            if info.user_exits:
                ue_names = ", ".join(ue["class"] for ue in info.user_exits)
                lines.append(f"**User Exits:** {ue_names}")
            if info.events and info.events != "None":
                lines.append(f"**Events:** {info.events}")
            lines.append("")

        out_file = output_dir / f"api-{module}.md"
        out_file.write_text("\n".join(lines), encoding="utf-8")
        print(f"  {out_file.name} ({len(apis)} APIs)")

    # Also generate a master index
    index_lines = [
        "# Sterling API Reference — All APIs",
        "",
        f"Total: {len(_apis)} APIs across {len(_apis_by_module)} modules.",
        "",
        "| API | Module | Description |",
        "| --- | --- | --- |",
    ]
    for name in sorted(_apis):
        info = _apis[name]
        desc = info.description[:100] + "..." if len(info.description) > 100 else info.description
        desc = desc.replace("|", "\\|").replace("\n", " ")
        index_lines.append(f"| {name} | {info.module} | {desc} |")

    index_file = output_dir / "api-index.md"
    index_file.write_text("\n".join(index_lines), encoding="utf-8")
    print(f"  {index_file.name} (master index)")

    print(f"\nExported {len(_apis)} API summaries to {output_dir}")
    print("Ingest these into the product-docs RAG server.")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print(f"Indexed {len(_apis)} APIs across {len(_apis_by_module)} modules")
        for mod in sorted(_apis_by_module):
            print(f"  {mod}: {len(_apis_by_module[mod])} APIs")
        print(f"\nSample: {list(_apis.keys())[:5]}")
        if _apis:
            sample = next(iter(_apis.values()))
            print(f"\n--- {sample.name} ---")
            print(f"Description: {sample.description[:100]}...")
            print(f"User exits: {len(sample.user_exits)}")
            print(f"Events: {sample.events}")
            print(f"Input XML: {sample.input_xsd_html}")
    elif len(sys.argv) > 1 and sys.argv[1] == "--export-summaries":
        out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("api-summaries")
        _export_summaries(out)
    else:
        server.run()
