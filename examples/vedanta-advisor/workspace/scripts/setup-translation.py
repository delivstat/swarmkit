# /// script
# requires-python = ">=3.10"
# dependencies = ["torch>=2.0,<2.7", "transformers>=4.40,<5.0", "sentencepiece>=0.2", "langdetect>=1.0"]
# ///
"""Download and cache translation models + install language detection.

One-time setup:
- langdetect: lightweight language detection (no model download)
- IndicTrans2: en↔indic translation (~2GB model download)

Usage:
    cd /home/srijith/dev/vedanta-advisor
    uv run workspace/scripts/setup-translation.py
"""

import os
import sys


def main() -> None:
    print("=== Translation Model Setup ===")
    print(f"Cache dir: {os.environ.get('HF_HOME', '~/.cache/huggingface/')}")
    print()

    print("1/2 Testing langdetect (language detection)...")
    try:
        from langdetect import detect

        lang = detect("नमस्ते, मैं कैसे मदद कर सकता हूँ?")
        print(f"  ✓ langdetect ready — test: {lang}")
    except Exception as e:
        print(f"  ✗ langdetect failed: {e}", file=sys.stderr)
        sys.exit(1)

    print()

    model_name = os.environ.get("INDICTRANS_MODEL", "ai4bharat/indictrans2-en-indic-1B")
    print(f"2/2 Downloading IndicTrans2 ({model_name}, ~2GB)...")
    try:
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
        model = AutoModelForSeq2SeqLM.from_pretrained(model_name, trust_remote_code=True)
        print(f"  ✓ IndicTrans2 ready — vocab size: {tokenizer.vocab_size}")
    except Exception as e:
        print(f"  ✗ IndicTrans2 failed: {e}", file=sys.stderr)
        sys.exit(1)

    print()
    print("=== Setup complete ===")
    print("Models cached. The translation MCP server will use these on startup.")


if __name__ == "__main__":
    main()
