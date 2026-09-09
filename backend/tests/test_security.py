"""Identity verification for order lookups.

These tests encode the security property the order tool depends on: an order
code alone must not unlock an order, and a partial phone number must not either.
"""

import pytest

from app.security import (
    contact_matches,
    hash_email,
    hash_phone,
    mask_phone,
    normalize_email,
    normalize_phone,
)

CANONICAL = "0901234567"


@pytest.mark.parametrize(
    "written",
    [
        "0901234567",
        "0901 234 567",
        "090-123-4567",
        "+84 901 234 567",
        "+84901234567",
        "84901234567",
        "0084901234567",
        "901234567",
        "  0901234567  ",
    ],
)
def test_phone_formats_converge(written):
    """Every way a Vietnamese number gets typed must hash identically."""
    assert normalize_phone(written) == CANONICAL


def test_email_normalisation_ignores_case_and_padding():
    assert normalize_email("  Nguyen.A@Example.COM ") == "nguyen.a@example.com"


class TestContactVerification:
    phone_hash = hash_phone(CANONICAL)
    email_hash = hash_email("buyer@example.com")

    def _check(self, supplied):
        return contact_matches(
            supplied=supplied, phone_hash=self.phone_hash, email_hash=self.email_hash
        )

    def test_correct_phone_in_any_format_verifies(self):
        assert self._check("+84 901 234 567")

    def test_correct_email_verifies_case_insensitively(self):
        assert self._check("Buyer@Example.com")

    def test_wrong_phone_rejected(self):
        assert not self._check("0909999999")

    def test_wrong_email_rejected(self):
        assert not self._check("someone@example.com")

    @pytest.mark.parametrize("fragment", ["4567", "567", "234567", "090"])
    def test_partial_phone_rejected(self, fragment):
        """A four digit suffix would be brute forceable in 10,000 guesses."""
        assert not self._check(fragment)

    @pytest.mark.parametrize("empty", ["", "   ", None])
    def test_empty_contact_rejected(self, empty):
        assert not self._check(empty)


def test_masked_phone_hides_everything_but_the_last_four():
    masked = mask_phone("4567")
    assert masked.endswith("4567")
    assert "0901" not in masked
    assert set(masked[:-4]) == {"*"}
