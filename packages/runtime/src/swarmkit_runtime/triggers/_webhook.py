"""Webhook signature validation utilities.

Supports HMAC-SHA256 (GitHub-style ``sha256=<hex>`` format) and can be
extended to other algorithms as needed.
"""

from __future__ import annotations

import hashlib
import hmac
import logging

logger = logging.getLogger("swarmkit.triggers.webhook")


def validate_webhook_signature(
    body: bytes,
    signature: str,
    secret: str,
    algorithm: str = "sha256",
) -> bool:
    """Validate an HMAC webhook signature.

    Parameters
    ----------
    body:
        Raw request body bytes to authenticate.
    signature:
        Signature string from the request header.
        Expected format: ``sha256=<hex-digest>`` (GitHub style).
    secret:
        Shared secret used to compute the expected HMAC.
    algorithm:
        Hash algorithm to use. Currently only ``"sha256"`` is supported.

    Returns
    -------
    bool
        ``True`` if the signature matches, ``False`` otherwise.
        Never raises; invalid / malformed input returns ``False``.
    """
    if algorithm != "sha256":
        logger.warning("Unsupported webhook signature algorithm: %r", algorithm)
        return False

    prefix = f"{algorithm}="
    if not signature.startswith(prefix):
        logger.debug(
            "Webhook signature %r does not start with expected prefix %r",
            signature[:20],
            prefix,
        )
        return False

    provided_hex = signature[len(prefix) :]

    try:
        expected = hmac.new(
            secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
    except Exception:
        logger.warning("Failed to compute HMAC for webhook validation", exc_info=True)
        return False

    return hmac.compare_digest(expected, provided_hex)


__all__ = ["validate_webhook_signature"]
