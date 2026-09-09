"""Reciprocal Rank Fusion, the hybrid-search merge step."""

import pytest

from app.db.vector import rrf_merge


def _skus(results):
    return [result["sku"] for result in results]


def test_document_ranked_well_by_both_retrievers_wins():
    semantic = [{"sku": "A"}, {"sku": "B"}, {"sku": "C"}]
    keyword = [{"sku": "C"}, {"sku": "A"}, {"sku": "D"}]
    # A is rank 1 and 2; C is rank 3 and 1. A's total is higher.
    assert _skus(rrf_merge([semantic, keyword], key="sku", limit=4))[0] == "A"


def test_documents_unique_to_one_list_still_appear():
    semantic = [{"sku": "A"}]
    keyword = [{"sku": "Z"}]
    assert set(_skus(rrf_merge([semantic, keyword], key="sku", limit=5))) == {"A", "Z"}


def test_weights_shift_the_ranking():
    semantic = [{"sku": "S"}]
    keyword = [{"sku": "K"}]
    semantic_first = rrf_merge([semantic, keyword], key="sku", weights=[1.0, 0.1], limit=2)
    keyword_first = rrf_merge([semantic, keyword], key="sku", weights=[0.1, 1.0], limit=2)
    assert _skus(semantic_first)[0] == "S"
    assert _skus(keyword_first)[0] == "K"


def test_limit_is_respected():
    lists = [[{"sku": f"P{i}"} for i in range(20)]]
    assert len(rrf_merge(lists, key="sku", limit=5)) == 5


def test_richer_document_copy_is_kept():
    """Projections differ between retrievers; the merged row keeps real fields."""
    semantic = [{"sku": "A", "price": 100}]
    keyword = [{"sku": "A"}]
    merged = rrf_merge([semantic, keyword], key="sku", limit=1)
    assert merged[0]["price"] == 100


def test_scores_are_attached_and_descending():
    lists = [[{"sku": "A"}, {"sku": "B"}, {"sku": "C"}]]
    merged = rrf_merge(lists, key="sku", limit=3)
    scores = [result["_score"] for result in merged]
    assert scores == sorted(scores, reverse=True)


def test_mismatched_weight_count_is_rejected():
    with pytest.raises(ValueError):
        rrf_merge([[{"sku": "A"}], [{"sku": "B"}]], key="sku", weights=[1.0])


def test_documents_missing_the_key_are_skipped():
    merged = rrf_merge([[{"sku": "A"}, {"name": "no sku"}]], key="sku", limit=5)
    assert _skus(merged) == ["A"]
