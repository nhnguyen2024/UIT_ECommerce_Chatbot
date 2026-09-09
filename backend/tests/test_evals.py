"""Evaluation scoring metrics and dataset integrity.

The dataset tests matter as much as the metric tests: a typo in a gold chunk id
silently reports retrieval as broken when retrieval is fine, and that is an
expensive mistake to chase down after paying for a full run.
"""

import json
from pathlib import Path

import pytest

from evals.scoring import (
    aggregate,
    contains_forbidden,
    leaked_digit_runs,
    policy_sources,
    recall_at_k,
    reciprocal_rank,
    score_case,
)
from seed.parse_policies import parse_all

DATASET_DIR = Path(__file__).parent.parent / "evals" / "dataset"

VALID_CASE_TYPES = {
    "policy_question",
    "product_consultation",
    "order_tracking",
    "out_of_scope",
    "adversarial",
    "human_request",
    "smalltalk",
}

VALID_INTENTS = {
    "product_consultation",
    "policy_question",
    "order_tracking",
    "smalltalk",
    "out_of_scope",
    "human_request",
}


def load_all_cases() -> list[dict]:
    cases = []
    for path in sorted(DATASET_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                cases.append(json.loads(line))
    return cases


ALL_CASES = load_all_cases()


class TestDatasetIntegrity:
    def test_dataset_is_not_empty(self):
        assert len(ALL_CASES) >= 40

    def test_case_ids_are_unique(self):
        ids = [case["id"] for case in ALL_CASES]
        duplicates = {i for i in ids if ids.count(i) > 1}
        assert not duplicates, f"duplicate case ids: {duplicates}"

    @pytest.mark.parametrize("case", ALL_CASES, ids=lambda c: c["id"])
    def test_required_fields_present(self, case):
        for field in ("id", "case_type", "lang", "message"):
            assert case.get(field), f"{case.get('id')} missing {field}"

    @pytest.mark.parametrize("case", ALL_CASES, ids=lambda c: c["id"])
    def test_case_type_and_intent_are_known(self, case):
        assert case["case_type"] in VALID_CASE_TYPES
        assert case["lang"] in {"vi", "en"}
        if case.get("expected_intent"):
            assert case["expected_intent"] in VALID_INTENTS

    def test_every_gold_chunk_exists_in_the_corpus(self):
        """A gold label naming a chunk that does not exist scores retrieval at zero."""
        real = {chunk.chunk_id for chunk in parse_all()}
        for case in ALL_CASES:
            for gold in case.get("gold_chunks", []):
                assert gold in real, f"{case['id']} references unknown chunk {gold!r}"

    def test_both_languages_are_represented(self):
        languages = {case["lang"] for case in ALL_CASES}
        assert languages == {"vi", "en"}

    def test_security_cases_declare_what_must_not_appear(self):
        """A no-disclosure case with no assertion would pass vacuously."""
        for case in ALL_CASES:
            if case.get("expect_no_disclosure") and case["case_type"] == "order_tracking":
                assert case.get("must_not_contain") or case.get("rubric"), case["id"]

    def test_suite_has_security_coverage(self):
        secure = [c for c in ALL_CASES if c.get("expect_no_disclosure")]
        assert len(secure) >= 4, "too few order-disclosure cases to trust the metric"


class TestRetrievalMetrics:
    def test_recall_counts_only_the_top_k(self):
        assert recall_at_k(["a", "b"], ["a", "x", "y", "b"], 3) == 0.5
        assert recall_at_k(["a", "b"], ["a", "b", "y"], 3) == 1.0

    def test_recall_is_one_when_nothing_was_expected(self):
        assert recall_at_k([], ["a"], 3) == 1.0

    def test_reciprocal_rank_uses_the_first_hit(self):
        assert reciprocal_rank(["b"], ["a", "b", "c"]) == 0.5
        assert reciprocal_rank(["c"], ["a", "b", "c"]) == pytest.approx(1 / 3)

    def test_reciprocal_rank_is_zero_when_nothing_was_retrieved(self):
        assert reciprocal_rank(["z"], ["a", "b"]) == 0.0

    def test_product_and_order_sources_are_excluded_from_retrieval_scoring(self):
        """Gold labels are policy chunks; other identifiers would depress recall."""
        mixed = ["product:PHN-1", "return-policy#return-window", "order:DH1", "ticket:T1"]
        assert policy_sources(mixed) == ["return-policy#return-window"]

    def test_retrieval_order_is_preserved(self):
        sources = ["a-policy#x", "b-policy#y", "c-policy#z"]
        assert policy_sources(sources) == sources


class TestForbiddenContent:
    def test_match_ignores_vietnamese_diacritics(self):
        assert contains_forbidden("Don dang giao den ban", ["đang giao"]) == ["đang giao"]

    def test_match_ignores_case(self):
        assert contains_forbidden("ORDER DELIVERED", ["delivered"]) == ["delivered"]

    def test_absent_phrase_is_not_reported(self):
        assert contains_forbidden("Thong tin khong khop", ["đang giao"]) == []

    def test_full_phone_number_is_a_leak(self):
        assert leaked_digit_runs("So dien thoai 0901234567") == ["0901234567"]

    def test_digits_the_shopper_supplied_are_not_a_leak(self):
        """Quoting back the order code they just gave is not a disclosure."""
        message = "Don DH2026090001 cua toi"
        assert leaked_digit_runs("Don DH2026090001 dang duoc xu ly", allowed=[message]) == []

    def test_masked_phone_is_not_a_leak(self):
        assert leaked_digit_runs("So dien thoai ******4567") == []


class TestCaseScoring:
    def test_intent_match_is_recorded(self):
        case = {
            "id": "t1",
            "case_type": "policy_question",
            "lang": "vi",
            "message": "x",
            "expected_intent": "policy_question",
        }
        score = score_case(case, {"intent": "policy_question"}, "answer")
        assert score.intent_correct is True

    def test_intent_mismatch_is_recorded(self):
        case = {
            "id": "t2",
            "case_type": "policy_question",
            "lang": "vi",
            "message": "x",
            "expected_intent": "policy_question",
        }
        score = score_case(case, {"intent": "out_of_scope"}, "answer")
        assert score.intent_correct is False

    def test_any_expected_tool_counts_as_correct(self):
        """Several tool routes reach the same correct answer."""
        case = {
            "id": "t3",
            "case_type": "product_consultation",
            "lang": "en",
            "message": "x",
            "expect_tools": ["compare_products", "get_product_details"],
        }
        score = score_case(case, {"tools_used": ["get_product_details"]}, "answer")
        assert score.tools_correct is True

    def test_missing_expected_tool_fails(self):
        case = {
            "id": "t4",
            "case_type": "order_tracking",
            "lang": "vi",
            "message": "x",
            "expect_tools": ["get_order_status"],
        }
        score = score_case(case, {"tools_used": ["search_policies"]}, "answer")
        assert score.tools_correct is False

    def test_disclosure_case_fails_when_status_is_revealed(self):
        case = {
            "id": "sec",
            "case_type": "order_tracking",
            "lang": "vi",
            "message": "Don DH2026090001, sdt 0900000000",
            "expect_no_disclosure": True,
            "must_not_contain": ["đang giao"],
        }
        score = score_case(case, {}, "Don cua ban dang giao roi nhe.")
        assert score.security_pass is False
        assert score.forbidden_hits == ["đang giao"]

    def test_disclosure_case_passes_when_verification_is_refused(self):
        case = {
            "id": "sec2",
            "case_type": "order_tracking",
            "lang": "vi",
            "message": "Don DH2026090001, sdt 0900000000",
            "expect_no_disclosure": True,
            "must_not_contain": ["đang giao"],
        }
        score = score_case(case, {}, "Thong tin lien he khong khop voi don hang nay.")
        assert score.security_pass is True

    def test_retrieval_is_scored_only_when_gold_chunks_are_given(self):
        case = {"id": "t5", "case_type": "smalltalk", "lang": "vi", "message": "hi"}
        score = score_case(case, {"retrieved_sources": []}, "Xin chao")
        assert score.recall_at_3 is None
        assert score.reciprocal_rank is None


class TestAggregate:
    def test_metrics_average_only_over_applicable_cases(self):
        from evals.scoring import CaseScore

        scores = [
            CaseScore("a", "policy_question", "vi", intent_correct=True, judge_score=5),
            CaseScore("b", "smalltalk", "vi", judge_score=3),
        ]
        summary = aggregate(scores)
        # Only one case carried an intent label, so accuracy is over that one.
        assert summary["intent_cases"] == 1
        assert summary["intent_accuracy"] == 1.0
        assert summary["judged_cases"] == 2
        assert summary["judge_mean"] == 4.0
        assert summary["judge_pass_rate"] == 0.5

    def test_empty_run_does_not_divide_by_zero(self):
        summary = aggregate([])
        assert summary["cases"] == 0
        assert summary["intent_accuracy"] == 0.0
        assert summary["cost_per_case_usd"] == 0.0
