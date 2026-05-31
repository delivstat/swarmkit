# /// script
# requires-python = ">=3.11"
# dependencies = ["torch>=2.0", "transformers>=4.40", "sentencepiece>=0.2"]
# ///
"""Download and cache IndicLID + IndicTrans2 models.

One-time setup — downloads ~2-3GB of model weights to ~/.cache/huggingface/.
Subsequent runs of the translation MCP server use the cached models.

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

    print("1/2 Downloading IndicLID (language detection, ~500MB)...")
    try:
        from transformers import pipeline

        lid = pipeline("text-classification", model="ai4bharat/IndicLID", device=-1)
        result = lid("नमस्ते, मैं कैसे मदद कर सकता हूँ?")
        print(f"  ✓ IndicLID ready — test: {result[0]['label']}")
    except Exception as e:
        print(f"  ✗ IndicLID failed: {e}", file=sys.stderr)
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
