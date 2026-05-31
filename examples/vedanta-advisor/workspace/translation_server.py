# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.0", "deep-translator>=1.11", "langdetect>=1.0"]
# ///
"""Translation MCP Server — Google Translate via deep-translator.

Free, no API key, no model download. Supports all Indian languages.

Tools:
  - detect_language: identify the language of input text
  - translate_to_english: detect + translate to English
  - translate_from_english: translate English to a target language
"""

from __future__ import annotations

import json
import sys

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
except ImportError:
    print("Missing mcp. Run with: uv run translation_server.py", file=sys.stderr)
    sys.exit(1)

LANG_CODES = {
    "hi": "Hindi",
    "bn": "Bengali",
    "ta": "Tamil",
    "te": "Telugu",
    "kn": "Kannada",
    "ml": "Malayalam",
    "mr": "Marathi",
    "gu": "Gujarati",
    "pa": "Punjabi",
    "ur": "Urdu",
    "sa": "Sanskrit",
    "ne": "Nepali",
    "en": "English",
}

server = Server("translation")


def _detect(text: str) -> str:
    from langdetect import detect

    return detect(text)


def _translate(text: str, source: str, target: str) -> str:
    from deep_translator import GoogleTranslator

    return GoogleTranslator(source=source, target=target).translate(text)


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="detect_language",
            description="Detect the language of input text.",
            inputSchema={
                "type": "object",
                "required": ["text"],
                "properties": {"text": {"type": "string"}},
            },
        ),
        Tool(
            name="translate_to_english",
            description="Auto-detect language and translate to English.",
            inputSchema={
                "type": "object",
                "required": ["text"],
                "properties": {"text": {"type": "string"}},
            },
        ),
        Tool(
            name="translate_from_english",
            description="Translate English text to a target language.",
            inputSchema={
                "type": "object",
                "required": ["text", "target_language"],
                "properties": {
                    "text": {"type": "string"},
                    "target_language": {
                        "type": "string",
                        "description": "Target language: hindi, tamil, telugu, bengali, etc.",
                    },
                },
            },
        ),
    ]


def _resolve_code(lang: str) -> str:
    lang = lang.lower().strip()
    for code, name in LANG_CODES.items():
        if lang == code or lang == name.lower():
            return code
    return lang


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "detect_language":
        text = arguments.get("text", "")
        code = _detect(text)
        lang_name = LANG_CODES.get(code, code)
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {"language": code, "language_name": lang_name},
                    ensure_ascii=False,
                ),
            )
        ]

    if name == "translate_to_english":
        text = arguments.get("text", "")
        source = _detect(text)
        if source == "en":
            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "source_language": "en",
                            "translation": text,
                            "note": "Already in English",
                        },
                        ensure_ascii=False,
                    ),
                )
            ]
        translated = _translate(text, source, "en")
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {
                        "source_language": source,
                        "source_language_name": LANG_CODES.get(source, source),
                        "translation": translated,
                        "original": text,
                    },
                    ensure_ascii=False,
                ),
            )
        ]

    if name == "translate_from_english":
        text = arguments.get("text", "")
        target = _resolve_code(arguments.get("target_language", "hi"))
        translated = _translate(text, "en", target)
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {
                        "target_language": target,
                        "target_language_name": LANG_CODES.get(target, target),
                        "translation": translated,
                        "original": text,
                    },
                    ensure_ascii=False,
                ),
            )
        ]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
