"""Generation, parsing and hashing of public-API keys.

A key handed to a user looks like::

    seurch_sk_<prefix>.<secret>

* ``prefix`` - 8 hex chars, stored in the clear and **unique**, so a presented
  key is found with a single indexed lookup (no table scan).
* ``secret`` - 32 random URL-safe bytes (~256 bits of entropy). Only its
  SHA-256 hash is stored; the secret itself is shown to the user exactly once,
  at creation, and is unrecoverable afterwards.

Because the secret carries ~256 bits of entropy, a fast hash (SHA-256) is
sufficient and appropriate here: brute-forcing it is infeasible, and unlike a
human password the key is verified on *every* API request, so a slow KDF
(PBKDF2/argon2) would add latency to the hot path for no security gain. The
comparison is constant-time (``hmac.compare_digest``) to avoid leaking the hash
through timing.
"""

import hashlib
import hmac
import secrets

# Human-recognisable brand prefix. Keeps keys identifiable in logs and lets
# secret scanners spot a leaked Seurch key by its shape.
KEY_BRAND = 'seurch_sk_'

# Length of the clear-text lookup prefix, in hex characters.
PREFIX_LENGTH = 8


def generate_key():
    """Return ``(prefix, secret, full_key)`` for a brand-new API key.

    Only *prefix* and a hash of *secret* are persisted; *full_key* is the
    single value ever shown to the user.
    """
    prefix = secrets.token_hex(PREFIX_LENGTH // 2)  # PREFIX_LENGTH hex chars
    secret = secrets.token_urlsafe(32)
    full_key = f'{KEY_BRAND}{prefix}.{secret}'
    return prefix, secret, full_key


def hash_secret(secret: str) -> str:
    """SHA-256 hex digest of *secret* - what we store and compare against."""
    return hashlib.sha256(secret.encode('utf-8')).hexdigest()


def parse_key(full_key: str):
    """Split a presented key into ``(prefix, secret)``.

    Returns ``(None, None)`` for anything that isn't shaped like one of our
    keys, so callers can treat a malformed key exactly like a wrong one.
    """
    if not full_key:
        return None, None
    key = full_key.strip()
    key = key.removeprefix(KEY_BRAND)
    prefix, sep, secret = key.partition('.')
    if not sep or not prefix or not secret:
        return None, None
    return prefix, secret


def verify_secret(secret: str, hashed: str) -> bool:
    """Constant-time check that *secret* hashes to *hashed*."""
    return hmac.compare_digest(hash_secret(secret), hashed)
