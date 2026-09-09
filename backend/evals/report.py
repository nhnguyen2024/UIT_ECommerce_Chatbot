"""Render evaluation results as Markdown.

Output is written to sit in a report unedited: headline metrics first, then a
per-suite breakdown, then the failures worth reading. Passing cases are not
listed, because a wall of ticks tells the reader nothing.
"""

from __future__ import annotations

from collections import defaultdict


def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def render_markdown(payload: dict) -> str:
    summary = payload["summary"]
    cases = payload["cases"]
    lines: list[str] = []

    lines.append("# Evaluation results")
    lines.append("")
    tag = f" ({payload['tag']})" if payload.get("tag") else ""
    lines.append(f"Run at {payload['started_at']}{tag}")
    lines.append("")
    lines.append(
        f"Agent `{payload['agent_model']}`, classifier "
        f"`{payload['classifier_model']}`, embeddings `{payload['embedding_mode']}`. "
        f"{summary['cases']} cases in {payload['duration_seconds']}s."
    )
    lines.append("")

    lines.append("## Headline metrics")
    lines.append("")
    lines.append("| Metric | Value | Cases |")
    lines.append("|---|---|---|")
    lines.append(
        f"| Intent routing accuracy | {_percent(summary['intent_accuracy'])} "
        f"| {summary['intent_cases']} |"
    )
    lines.append(
        f"| Policy retrieval recall@3 | {_percent(summary['recall_at_3'])} "
        f"| {summary['retrieval_cases']} |"
    )
    lines.append(
        f"| Policy retrieval recall@5 | {_percent(summary['recall_at_5'])} "
        f"| {summary['retrieval_cases']} |"
    )
    lines.append(
        f"| Mean reciprocal rank | {summary['mrr']:.3f} | {summary['retrieval_cases']} |"
    )
    lines.append(
        f"| Tool selection | {_percent(summary['tool_selection'])} | {summary['tool_cases']} |"
    )
    lines.append(
        f"| Answer quality (judge, mean of 5) | {summary['judge_mean']:.2f} "
        f"| {summary['judged_cases']} |"
    )
    lines.append(
        f"| Answer quality pass rate (>=4) | {_percent(summary['judge_pass_rate'])} "
        f"| {summary['judged_cases']} |"
    )
    lines.append(
        f"| Groundedness | {_percent(summary['groundedness'])} | {summary['cases']} |"
    )
    lines.append(
        f"| Invalid citation rate | {_percent(summary['invalid_citation_rate'])} "
        f"| {summary['cases']} |"
    )
    lines.append(
        f"| Security pass rate | {_percent(summary['security_pass_rate'])} "
        f"| {summary['security_cases']} |"
    )
    lines.append(f"| Errors | {summary['errors']} | {summary['cases']} |")
    lines.append("")

    lines.append("## Cost and latency")
    lines.append("")
    lines.append("| Measure | Value |")
    lines.append("|---|---|")
    lines.append(f"| Average latency per turn | {summary['avg_latency_ms']} ms |")
    lines.append(f"| Total run cost | ${summary['total_cost_usd']:.4f} |")
    lines.append(f"| Cost per case | ${summary['cost_per_case_usd']:.5f} |")
    lines.append("")

    # --- Per suite ---------------------------------------------------------
    grouped: dict[str, list[dict]] = defaultdict(list)
    for case in cases:
        grouped[case["case_type"]].append(case)

    lines.append("## By case type")
    lines.append("")
    lines.append("| Case type | Cases | Intent | Judge mean | Grounded |")
    lines.append("|---|---|---|---|---|")
    for case_type in sorted(grouped):
        group = grouped[case_type]
        intents = [c for c in group if c["intent_correct"] is not None]
        judged = [c for c in group if c["judge_score"] is not None]
        intent_rate = (
            sum(1 for c in intents if c["intent_correct"]) / len(intents) if intents else 0.0
        )
        judge_mean = sum(c["judge_score"] for c in judged) / len(judged) if judged else 0.0
        grounded_rate = sum(1 for c in group if c["grounded"]) / len(group)
        lines.append(
            f"| {case_type} | {len(group)} | {_percent(intent_rate)} "
            f"| {judge_mean:.2f} | {_percent(grounded_rate)} |"
        )
    lines.append("")

    # --- By language -------------------------------------------------------
    by_language: dict[str, list[dict]] = defaultdict(list)
    for case in cases:
        by_language[case["lang"]].append(case)

    lines.append("## By language")
    lines.append("")
    lines.append("| Language | Cases | Judge mean | Grounded |")
    lines.append("|---|---|---|---|")
    for language in sorted(by_language):
        group = by_language[language]
        judged = [c for c in group if c["judge_score"] is not None]
        judge_mean = sum(c["judge_score"] for c in judged) / len(judged) if judged else 0.0
        grounded_rate = sum(1 for c in group if c["grounded"]) / len(group)
        label = "Vietnamese" if language == "vi" else "English"
        lines.append(
            f"| {label} | {len(group)} | {judge_mean:.2f} | {_percent(grounded_rate)} |"
        )
    lines.append("")

    # --- Failures ----------------------------------------------------------
    security_failures = [c for c in cases if c["security_pass"] is False]
    if security_failures:
        lines.append("## Security failures")
        lines.append("")
        lines.append(
            "These are the cases that matter most: the assistant disclosed "
            "something it should have withheld."
        )
        lines.append("")
        for case in security_failures:
            detail = ", ".join(case["forbidden_hits"] + case["digit_leaks"])
            lines.append(f"- `{case['case_id']}` leaked: {detail}")
            lines.append(f"  > {case['answer'][:300]}")
        lines.append("")

    failures = [
        c
        for c in cases
        if c["error"]
        or (c["judge_score"] is not None and c["judge_score"] < 4)
        or c["intent_correct"] is False
        or not c["grounded"]
    ]
    if failures:
        lines.append("## Cases to review")
        lines.append("")
        for case in sorted(failures, key=lambda c: c["judge_score"] or 0):
            bits = []
            if case["error"]:
                bits.append(f"error: {case['error']}")
            if case["judge_score"] is not None and case["judge_score"] < 4:
                bits.append(f"judge {case['judge_score']}/5: {case['judge_reason']}")
            if case["intent_correct"] is False:
                bits.append(
                    f"intent {case['observed_intent']} expected {case['expected_intent']}"
                )
            if not case["grounded"]:
                bits.append(f"unsupported figures: {case['unsupported_figures']}")
            if case["invalid_citations"]:
                bits.append(f"invalid citations: {case['invalid_citations']}")
            lines.append(f"- `{case['case_id']}` ({case['case_type']}) — " + "; ".join(bits))
        lines.append("")
    else:
        lines.append("No failing cases.")
        lines.append("")

    return "\n".join(lines)
