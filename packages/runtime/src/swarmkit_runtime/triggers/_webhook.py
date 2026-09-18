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


#: Default header per webhook auth method (`trigger.schema.json` `webhook_auth.method`).
_DEFAULT_HEADER = {
    "hmac": "X-Hub-Signature-256",
    "bearer": "Authorization",
    "api_key": "X-API-Key",
}


def validate_webhook_auth(method: str, header_value: str, body: bytes, secret: str) -> bool:
    """Check one delivery against the trigger's declared auth method.

    ``hmac`` verifies a signature over the body (GitHub style); ``bearer`` expects
    ``Authorization: Bearer <secret>``; ``api_key`` expects the header to carry the secret
    itself. The schema accepted all three since triggers landed and only ``hmac`` was ever
    checked — a `bearer` trigger compared a token against an HMAC and refused everything.
    Comparisons are constant-time. Unknown methods fail closed.
    """
    if method == "hmac":
        return validate_webhook_signature(body, header_value, secret)
    if method == "bearer":
        presented = header_value.strip()
        if presented.lower().startswith("bearer "):
            presented = presented[7:].strip()
        return bool(presented) and hmac.compare_digest(presented, secret)
    if method == "api_key":
        presented = header_value.strip()
        return bool(presented) and hmac.compare_digest(presented, secret)
    return False


def default_auth_header(method: str) -> str:
    return _DEFAULT_HEADER.get(method, "X-Hub-Signature-256")
