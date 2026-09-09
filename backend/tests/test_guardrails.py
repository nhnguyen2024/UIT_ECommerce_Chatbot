"""Input screening, citation validation, and numeric grounding."""

import pytest

from app.agent.guardrails import (
    GROUPED_NUMBER,
    check_citations,
    check_numeric_grounding,
    collect_numbers,
    screen_input,
)


class TestInputScreening:
    def test_credential_sharing_is_answered_with_a_fixed_warning(self):
        """An improvised reply about OTPs is a risk not worth taking."""
        verdict = screen_input(
            safety="sensitive_credentials", intent="order_tracking", confidence=0.9, lang="vi"
        )
        assert not verdict.allowed
        assert "OTP" in verdict.canned_reply

    def test_credential_warning_follows_the_conversation_language(self):
        english = screen_input(
            safety="sensitive_credentials", intent="smalltalk", confidence=0.9, lang="en"
        )
        assert "banking password" in english.canned_reply

    def test_confident_out_of_scope_is_declined_without_an_agent_call(self):
        verdict = screen_input(
            safety="out_of_scope", intent="out_of_scope", confidence=0.95, lang="en"
        )
        assert not verdict.allowed
        assert verdict.reason == "out_of_scope"

    def test_unconfident_out_of_scope_still_reaches_the_agent(self):
        verdict = screen_input(
            safety="out_of_scope", intent="out_of_scope", confidence=0.4, lang="vi"
        )
        assert verdict.allowed

    def test_classifier_fallback_never_short_circuits(self):
        """A failed classifier scores 0.0 and must not start declining real questions."""
        verdict = screen_input(
            safety="out_of_scope", intent="out_of_scope", confidence=0.0, lang="vi"
        )
        assert verdict.allowed

    def test_injection_attempt_is_passed_to_the_agent(self):
        """The system prompt handles this better than a keyword rule."""
        verdict = screen_input(
            safety="injection_attempt", intent="policy_question", confidence=0.9, lang="en"
        )
        assert verdict.allowed

    @pytest.mark.parametrize(
        "intent", ["product_consultation", "policy_question", "order_tracking", "human_request"]
    )
    def test_normal_intents_are_allowed(self, intent):
        assert screen_input(safety="ok", intent=intent, confidence=0.9, lang="vi").allowed


class TestCitations:
    def test_valid_marker_is_kept_and_structured(self):
        result = check_citations(
            "Doi tra trong 7 ngay. [ref:return-policy#return-window]",
            {"return-policy#return-window"},
        )
        assert "[ref:return-policy#return-window]" in result.text
        assert result.citations == [
            {"source_id": "return-policy#return-window", "kind": "policy"}
        ]
        assert result.invalid == []

    def test_fabricated_marker_is_removed(self):
        """A citation to nothing looks like evidence for a claim that has none."""
        result = check_citations("Gia 1.290.000d. [ref:product:FAKE-999]", set())
        assert "FAKE-999" not in result.text
        assert result.invalid == ["product:FAKE-999"]
        assert result.citations == []

    def test_removal_does_not_leave_stray_spacing(self):
        result = check_citations("Xin chao [ref:bad] ban.", set())
        assert "  " not in result.text
        assert result.text == "Xin chao ban."

    def test_repeated_marker_yields_one_citation(self):
        result = check_citations(
            "A [ref:return-policy#return-window] B [ref:return-policy#return-window]",
            {"return-policy#return-window"},
        )
        assert len(result.citations) == 1

    @pytest.mark.parametrize(
        "source_id,kind",
        [
            ("return-policy#refund-timing", "policy"),
            ("product:PHN-000", "product"),
            ("order:DH2026090001", "order"),
            ("ticket:TK123", "ticket"),
        ],
    )
    def test_citation_kind_is_derived_from_the_identifier(self, source_id, kind):
        result = check_citations(f"x [ref:{source_id}]", {source_id})
        assert result.citations[0]["kind"] == kind

    def test_text_without_markers_is_unchanged(self):
        text = "Xin chao ban, minh co the giup gi?"
        assert check_citations(text, set()).text == text


class TestNumberPattern:
    @pytest.mark.parametrize(
        "written,expected",
        [
            ("7.159.000d", ["7.159.000"]),
            ("1.290.000đ", ["1.290.000"]),
            ("1,290,000 VND", ["1,290,000"]),
            ("gia 500.000 dong", ["500.000"]),
            ("tu 25.000 den 45.000", ["25.000", "45.000"]),
        ],
    )
    def test_prices_match_even_with_an_attached_currency_mark(self, written, expected):
        """Vietnamese prices are written as 1.290.000d, with no separating space."""
        assert GROUPED_NUMBER.findall(written) == expected

    def test_bare_small_integers_are_not_treated_as_claims(self):
        assert GROUPED_NUMBER.findall("co 3 san pham") == []


class TestNumericGrounding:
    def test_price_quoted_from_a_tool_result_is_grounded(self):
        check = check_numeric_grounding(
            "Gia may nay la 7.159.000d.", [{"price": 7159000}]
        )
        assert check.grounded

    def test_invented_price_is_flagged(self):
        check = check_numeric_grounding("Gia chi 2.500.000d thoi.", [{"price": 7159000}])
        assert not check.grounded
        assert "2.500.000" in check.unsupported

    def test_duration_from_a_retrieved_passage_is_grounded(self):
        check = check_numeric_grounding(
            "Ban co the doi tra trong 7 ngay.",
            [{"text": "Khach hang co quyen doi tra trong vong 7 ngay."}],
        )
        assert check.grounded

    def test_invented_duration_is_flagged(self):
        check = check_numeric_grounding(
            "Ban co the doi tra trong 30 ngay.",
            [{"text": "trong vong 7 ngay"}],
        )
        assert not check.grounded

    def test_hotline_number_is_not_treated_as_a_claim(self):
        check = check_numeric_grounding("Goi hotline 1900 1234 nhe.", [])
        assert check.grounded

    def test_answer_with_no_figures_is_grounded(self):
        assert check_numeric_grounding("Minh se kiem tra giup ban.", []).grounded

    def test_numbers_are_collected_from_nested_structures(self):
        numbers = collect_numbers({"a": [{"price": 500000}], "b": {"c": "trong 15 ngay"}})
        assert "500000" in numbers
        assert "15" in numbers

    def test_booleans_are_not_collected_as_numbers(self):
        """bool is a subclass of int; True must not become the figure 1."""
        assert "1" not in collect_numbers({"verified": True})
