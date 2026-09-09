"""Contact hashing for order identity verification.

Orders are looked up by an order code. An order code alone must never be enough
to see an order: codes are short, sequential-looking, and get shared in
screenshots, so anyone could enumerate them and read other customers' names,
addresses, and purchase history. `get_order_status` therefore also requires a
contact detail that matches the order.

Contact details are stored only as salted hashes. The database never holds a
readable phone number or email address, and neither does anything the model
sees. Verification hashes what the caller supplied and compares digests.
"""

from __future__ import annotations

import hashlib
import hmac
import re

# Salt for the contact hashes. This is not a password store: the value being
# hashed has low entropy (a 10 digit phone number), so a salt is what stops a
# leaked database from being reversed with a rainbow table. It must stay stable,
# because changing it invalidates every stored hash.
_CONTACT_SALT = "uit-ecommerce-chatbot-contact-v1"

_NON_DIGITS = re.compile(r"\D")


def normalize_phone(raw: str) -> str:
    """Reduce a Vietnamese phone number to a canonical 0XXXXXXXXX form.

    Users type numbers many ways: "0901 234 567", "+84 90 123 4567",
    "84901234567". All of these are the same number and must hash identically.
    """
    digits = _NON_DIGITS.sub("", raw or "")

    # Strip the country code, with or without a leading zero after it.
    if digits.startswith("84") and len(digits) >= 11:
        digits = "0" + digits[2:]
    if digits.startswith("0084"):
        digits = "0" + digits[4:]

    # A bare 9 digit number is missing its trunk prefix.
    if len(digits) == 9 and not digits.startswith("0"):
        digits = "0" + digits

    return digits


def normalize_email(raw: str) -> str:
    return (raw or "").strip().lower()


def hash_phone(raw: str) -> str:
    return _digest("phone", normalize_phone(raw))


def hash_email(raw: str) -> str:
    return _digest("email", normalize_email(raw))


def _digest(kind: str, value: str) -> str:
    return hashlib.sha256(f"{_CONTACT_SALT}:{kind}:{value}".encode("utf-8")).hexdigest()


def contact_matches(*, supplied: str, phone_hash: str, email_hash: str) -> bool:
    """True when the supplied contact detail belongs to the order.

    Accepts either a phone number or an email address; the caller does not have
    to say which. Comparison uses `hmac.compare_digest` so the duration of a
    failed check does not leak how much of the digest matched.
    """
    if not supplied or not supplied.strip():
        return False

    candidate = supplied.strip()

    if "@" in candidate:
        return hmac.compare_digest(hash_email(candidate), email_hash)

    normalized = normalize_phone(candidate)
    # Reject fragments. A caller supplying "4567" must not be able to brute
    # force an order in 10,000 guesses; require a full Vietnamese mobile number.
    if len(normalized) < 10:
        return False

    return hmac.compare_digest(hash_phone(candidate), phone_hash)


def mask_phone(last4: str) -> str:
    """Render a confirmation the customer recognises without disclosing the number."""
    return f"******{last4}"
