"""Tests for the document reader MCP server.

Tests cover stdlib-based tools directly and verify optional-dependency
tools return clear install instructions when libraries are missing.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
import swarmkit_runtime.docs_reader._server as _mod
from swarmkit_runtime.docs_reader._server import (
    _resolve_path,
    list_files,
    read_csv,
    read_drawio,
    read_svg,
    read_text,
)


@pytest.fixture()
def tmp_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(_mod, "_workspace_root", tmp_path)
    return tmp_path


# ---- path resolution -------------------------------------------------------


def test_resolve_absolute_path(tmp_workspace: Path) -> None:
    p = tmp_workspace / "test.txt"
    p.write_text("hello")
    result = _resolve_path(str(p))
    assert result == p


def test_resolve_relative_path(tmp_workspace: Path) -> None:
    (tmp_workspace / "docs").mkdir()
    p = tmp_workspace / "docs" / "test.txt"
    p.write_text("hello")
    result = _resolve_path("docs/test.txt")
    assert result == p


# ---- read_text --------------------------------------------------------------


def test_read_text_basic(tmp_workspace: Path) -> None:
    p = tmp_workspace / "hello.txt"
    p.write_text("line one\nline two\nline three\n")
    result = read_text(str(p))
    assert "hello.txt (3 lines)" in result
    assert "1\tline one" in result
    assert "3\tline three" in result


def test_read_text_range(tmp_workspace: Path) -> None:
    p = tmp_workspace / "big.txt"
    p.write_text("\n".join(f"line {i}" for i in range(1, 101)))
    result = read_text(str(p), start_line=50, end_line=55)
    assert "line 50" in result
    assert "line 55" in result
    assert "line 56" not in result


def test_read_text_missing_file(tmp_workspace: Path) -> None:
    result = read_text(str(tmp_workspace / "nope.txt"))
    assert "ERROR: file not found" in result


def test_read_text_truncation(tmp_workspace: Path) -> None:
    p = tmp_workspace / "huge.txt"
    p.write_text("\n".join(f"line {i}" for i in range(1, 2000)))
    result = read_text(str(p))
    assert "TRUNCATED" in result


# ---- read_csv ---------------------------------------------------------------


def test_read_csv_basic(tmp_workspace: Path) -> None:
    p = tmp_workspace / "data.csv"
    p.write_text("name,age,city\nAlice,30,NYC\nBob,25,LA\n")
    result = read_csv(str(p))
    assert "| name | age | city |" in result
    assert "| Alice | 30 | NYC |" in result
    assert "| Bob | 25 | LA |" in result


def test_read_csv_custom_delimiter(tmp_workspace: Path) -> None:
    p = tmp_workspace / "data.tsv"
    p.write_text("name\tage\nAlice\t30\n")
    result = read_csv(str(p), delimiter="\t")
    assert "| name | age |" in result
    assert "| Alice | 30 |" in result


def test_read_csv_truncation(tmp_workspace: Path) -> None:
    p = tmp_workspace / "big.csv"
    lines = ["col1,col2"] + [f"r{i},v{i}" for i in range(200)]
    p.write_text("\n".join(lines))
    result = read_csv(str(p), max_rows=10)
    assert "TRUNCATED" in result


def test_read_csv_empty(tmp_workspace: Path) -> None:
    p = tmp_workspace / "empty.csv"
    p.write_text("")
    result = read_csv(str(p))
    assert "empty" in result.lower()


def test_read_csv_missing_file(tmp_workspace: Path) -> None:
    result = read_csv(str(tmp_workspace / "nope.csv"))
    assert "ERROR: file not found" in result


# ---- read_drawio ------------------------------------------------------------


def test_read_drawio_basic(tmp_workspace: Path) -> None:
    p = tmp_workspace / "flow.drawio"
    p.write_text(
        dedent("""\
        <?xml version="1.0" encoding="UTF-8"?>
        <mxfile>
          <diagram name="Page-1">
            <mxGraphModel>
              <root>
                <mxCell id="0"/>
                <mxCell id="1" parent="0"/>
                <mxCell id="2" value="Start" vertex="1" parent="1">
                  <mxGeometry x="100" y="100" width="80" height="40" as="geometry"/>
                </mxCell>
                <mxCell id="3" value="End" vertex="1" parent="1">
                  <mxGeometry x="300" y="100" width="80" height="40" as="geometry"/>
                </mxCell>
                <mxCell id="4" value="proceed" edge="1" source="2" target="3" parent="1"/>
              </root>
            </mxGraphModel>
          </diagram>
        </mxfile>
    """)
    )
    result = read_drawio(str(p))
    assert "Start" in result
    assert "End" in result
    assert "proceed" in result
    assert "→" in result


def test_read_drawio_no_elements(tmp_workspace: Path) -> None:
    p = tmp_workspace / "empty.drawio"
    p.write_text('<?xml version="1.0"?><mxfile></mxfile>')
    result = read_drawio(str(p))
    assert "no diagram elements" in result


def test_read_drawio_missing_file(tmp_workspace: Path) -> None:
    result = read_drawio(str(tmp_workspace / "nope.drawio"))
    assert "ERROR: file not found" in result


# ---- read_svg ---------------------------------------------------------------


def test_read_svg_with_text(tmp_workspace: Path) -> None:
    p = tmp_workspace / "arch.svg"
    p.write_text(
        dedent("""\
        <?xml version="1.0"?>
        <svg xmlns="http://www.w3.org/2000/svg">
          <title>Architecture Diagram</title>
          <text x="10" y="20">API Gateway</text>
          <text x="10" y="40">Database</text>
        </svg>
    """)
    )
    result = read_svg(str(p))
    assert "Architecture Diagram" in result
    assert "API Gateway" in result
    assert "Database" in result


def test_read_svg_no_text(tmp_workspace: Path) -> None:
    p = tmp_workspace / "graphic.svg"
    p.write_text(
        dedent("""\
        <?xml version="1.0"?>
        <svg xmlns="http://www.w3.org/2000/svg">
          <rect x="0" y="0" width="100" height="100"/>
        </svg>
    """)
    )
    result = read_svg(str(p))
    assert "purely graphical" in result


def test_read_svg_missing_file(tmp_workspace: Path) -> None:
    result = read_svg(str(tmp_workspace / "nope.svg"))
    assert "ERROR: file not found" in result


# ---- list_files -------------------------------------------------------------


def test_list_files_basic(tmp_workspace: Path) -> None:
    (tmp_workspace / "a.txt").write_text("a")
    (tmp_workspace / "b.pdf").write_bytes(b"pdf")
    (tmp_workspace / "sub").mkdir()
    (tmp_workspace / "sub" / "c.txt").write_text("c")
    result = list_files(str(tmp_workspace))
    assert "a.txt" in result
    assert "b.pdf" in result
    assert "c.txt" not in result  # not recursive by default


def test_list_files_recursive(tmp_workspace: Path) -> None:
    (tmp_workspace / "sub").mkdir()
    (tmp_workspace / "sub" / "deep.txt").write_text("deep")
    result = list_files(str(tmp_workspace), pattern="*.txt", recursive=True)
    assert "deep.txt" in result


def test_list_files_pattern_filter(tmp_workspace: Path) -> None:
    (tmp_workspace / "a.txt").write_text("a")
    (tmp_workspace / "b.pdf").write_bytes(b"pdf")
    result = list_files(str(tmp_workspace), pattern="*.pdf")
    assert "b.pdf" in result
    assert "a.txt" not in result


def test_list_files_empty_dir(tmp_workspace: Path) -> None:
    (tmp_workspace / "empty").mkdir()
    result = list_files(str(tmp_workspace / "empty"))
    assert "No files" in result


def test_list_files_missing_dir(tmp_workspace: Path) -> None:
    result = list_files(str(tmp_workspace / "nope"))
    assert "ERROR: directory not found" in result


# ---- optional dependency error messages -------------------------------------


def test_read_pdf_missing_dep_message(tmp_workspace: Path) -> None:
    """If pymupdf is missing, read_pdf should return install instructions."""
    p = tmp_workspace / "test.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    result = _mod.read_pdf(str(p))
    if "pymupdf" in result.lower() and "install" in result.lower():
        pass  # pymupdf not installed — got the expected message
    else:
        assert "Page 1" in result or "ERROR" in result  # pymupdf IS installed


def test_read_docx_missing_dep_message(tmp_workspace: Path) -> None:
    p = tmp_workspace / "test.docx"
    p.write_bytes(b"PK fake")
    result = _mod.read_docx(str(p))
    if "python-docx" in result.lower() and "install" in result.lower():
        pass  # python-docx not installed
    else:
        assert "ERROR" in result or "test.docx" in result


def test_read_excel_missing_dep_message(tmp_workspace: Path) -> None:
    p = tmp_workspace / "test.xlsx"
    p.write_bytes(b"PK fake")
    result = _mod.read_excel(str(p))
    if "openpyxl" in result.lower() and "install" in result.lower():
        pass  # openpyxl not installed
    else:
        assert "ERROR" in result or "test.xlsx" in result


def test_read_image_missing_dep_message(tmp_workspace: Path) -> None:
    p = tmp_workspace / "test.png"
    p.write_bytes(b"\x89PNG fake")
    result = _mod.read_image(str(p))
    if "pillow" in result.lower() or "pytesseract" in result.lower():
        pass  # dependency missing
    else:
        assert "ERROR" in result or "test.png" in result
