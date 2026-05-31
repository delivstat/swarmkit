# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.0", "torch>=2.0,<2.7", "transformers>=4.40,<5.0", "sentencepiece>=0.2", "langdetect>=1.0"]
# ///
"""Translation MCP Server — IndicLID + IndicTrans2 for Vedanta Advisor.

Provides automatic language detection and translation between English
and 22 Indian languages. Used by the advisor agent to support
multilingual conversations — reasoning happens in English, user
interface in any supported Indian language.

Tools:
  - detect_language: identify the language of input text
  - translate: translate text between any supported language pair
  - translate_to_english: detect language + translate to English
  - translate_from_english: translate English to a target language
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
except ImportError:
    print(
        "Missing dependencies. Run with:\n"
        "  uv run translation_server.py",
        file=sys.stderr,
    )
    sys.exit(1)


INDIC_LANGUAGES = {
    "hin_Deva": "Hindi",
    "ben_Beng": "Bengali",
    "tam_Taml": "Tamil",
    "tel_Telu": "Telugu",
    "kan_Knda": "Kannada",
    "mal_Mlym": "Malayalam",
    "mar_Deva": "Marathi",
    "guj_Gujr": "Gujarati",
    "pan_Guru": "Punjabi",
    "ori_Orya": "Odia",
    "asm_Beng": "Assamese",
    "urd_Arab": "Urdu",
    "san_Deva": "Sanskrit",
    "kas_Arab": "Kashmiri",
    "npi_Deva": "Nepali",
    "kok_Deva": "Konkani",
    "mai_Deva": "Maithili",
    "doi_Deva": "Dogri",
    "mni_Mtei": "Manipuri",
    "brx_Deva": "Bodo",
    "sat_Olck": "Santali",
    "snd_Deva": "Sindhi",
    "eng_Latn": "English",
}

LANG_NAME_TO_CODE = {v.lower(): k for k, v in INDIC_LANGUAGES.items()}

server = Server("translation")

_trans_model = None
_trans_tokenizer = None


LANGDETECT_TO_INDIC = {
    "hi": "hin_Deva",
    "bn": "ben_Beng",
    "ta": "tam_Taml",
    "te": "tel_Telu",
    "kn": "kan_Knda",
    "ml": "mal_Mlym",
    "mr": "mar_Deva",
    "gu": "guj_Gujr",
    "pa": "pan_Guru",
    "ur": "urd_Arab",
    "ne": "npi_Deva",
    "sa": "san_Deva",
    "en": "eng_Latn",
}


def _detect_language(text: str) -> str | None:
    """Detect language using langdetect (lightweight, no model download)."""
    try:
        from langdetect import detect

        lang_code = detect(text)
        return LANGDETECT_TO_INDIC.get(lang_code, lang_code)
    except Exception as e:
        print(f"Language detection failed: {e}", file=sys.stderr)
        return None


def _load_translator():
    """Load IndicTrans2 translation model."""
    global _trans_model, _trans_tokenizer  # noqa: PLW0603
    if _trans_model is not None:
        return _trans_model, _trans_tokenizer

    try:
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        model_name = os.environ.get(
            "INDICTRANS_MODEL",
            "ai4bharat/indictrans2-en-indic-1B",
        )
        _trans_tokenizer = AutoTokenizer.from_pretrained(
            model_name, trust_remote_code=True
        )
        _trans_model = AutoModelForSeq2SeqLM.from_pretrained(
            model_name, trust_remote_code=True
        )
        return _trans_model, _trans_tokenizer
    except Exception as e:
        print(f"Failed to load IndicTrans2: {e}", file=sys.stderr)
        return None, None


def _detect_language_full(text: str) -> dict:
    """Detect language using langdetect and return structured result."""
    lang_code = _detect_language(text)
    if lang_code is None:
        return {"language": "eng_Latn", "language_name": "English", "confidence": 0.0}

    lang_name = INDIC_LANGUAGES.get(lang_code, lang_code)
    return {
        "language": lang_code,
        "language_name": lang_name,
        "confidence": 0.95,
    }


def _translate_text(text: str, src_lang: str, tgt_lang: str) -> str:
    """Translate using IndicTrans2."""
    model, tokenizer = _load_translator()
    if model is None:
        return f"[Translation unavailable: IndicTrans2 not loaded. src={src_lang} tgt={tgt_lang}] {text}"

    try:
        inputs = tokenizer(
            text,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=512,
        )
        generated = model.generate(**inputs, max_new_tokens=512)
        result = tokenizer.batch_decode(generated, skip_special_tokens=True)[0]
        return result
    except Exception as e:
        return f"[Translation error: {e}] {text}"


@server.list_tools()
async def list_tools() -> list[Tool]:
    langs = ", ".join(INDIC_LANGUAGES.values())
    return [
        Tool(
            name="detect_language",
            description=f"Detect the language of input text. Supports: {langs}",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to identify"},
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="translate",
            description="Translate text between any supported language pair.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_language": {
                        "type": "string",
                        "description": "Source language code (e.g. hin_Deva) or name (e.g. Hindi)",
                    },
                    "target_language": {
                        "type": "string",
                        "description": "Target language code or name",
                    },
                },
                "required": ["text", "source_language", "target_language"],
            },
        ),
        Tool(
            name="translate_to_english",
            description=(
                "Auto-detect language and translate to English. "
                "Use this when the user's input might not be in English."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text in any Indian language"},
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="translate_from_english",
            description="Translate English text to a target Indian language.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "English text to translate"},
                    "target_language": {
                        "type": "string",
                        "description": "Target language code or name (e.g. Hindi, Tamil, hin_Deva)",
                    },
                },
                "required": ["text", "target_language"],
            },
        ),
    ]


def _resolve_lang_code(lang: str) -> str:
    """Resolve language name or code to IndicTrans2 code."""
    if lang in INDIC_LANGUAGES:
        return lang
    resolved = LANG_NAME_TO_CODE.get(lang.lower())
    if resolved:
        return resolved
    return lang


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    import json

    if name == "detect_language":
        result = _detect_language_full(arguments.get("text", ""))
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    elif name == "translate":
        src = _resolve_lang_code(arguments.get("source_language", ""))
        tgt = _resolve_lang_code(arguments.get("target_language", ""))
        result = _translate_text(arguments["text"], src, tgt)
        return [TextContent(type="text", text=result)]

    elif name == "translate_to_english":
        text = arguments.get("text", "")
        detected = _detect_language_full(text)
        src_lang = detected["language"]

        if src_lang == "eng_Latn":
            return [TextContent(
                type="text",
                text=json.dumps({
                    "detected_language": detected,
                    "translation": text,
                    "note": "Input is already English",
                }, ensure_ascii=False),
            )]

        translated = _translate_text(text, src_lang, "eng_Latn")
        return [TextContent(
            type="text",
            text=json.dumps({
                "detected_language": detected,
                "translation": translated,
            }, ensure_ascii=False),
        )]

    elif name == "translate_from_english":
        tgt = _resolve_lang_code(arguments.get("target_language", ""))
        result = _translate_text(arguments["text"], "eng_Latn", tgt)
        return [TextContent(type="text", text=result)]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
