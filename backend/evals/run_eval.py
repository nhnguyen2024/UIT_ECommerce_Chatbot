"""Evaluation runner.

Every run costs real money: each case is one full agent turn plus one judge
call. The runner therefore prints an estimate and waits for confirmation before
spending anything, and `--limit` exists so a change can be smoke tested over a
handful of cases before a full run.

Usage::

    python -m evals.run_eval --limit 5              # quick check
    python -m evals.run_eval --suite policy         # one suite
    python -m evals.run_eval --no-judge             # labels only, nearly free
    python -m evals.run_eval --yes                  # skip the confirmation

Results are written to evals/results/<timestamp>.json alongside a Markdown
summary that can be pasted into a report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from app.agent.orchestrator import run_turn
from app.config import get_settings
from app.db.client import close_client, ping
from evals.judges import judge_answer
from evals.report import render_markdown
from evals.scoring import CaseScore, aggregate, score_case

DATASET_DIR = Path(__file__).parent / "dataset"
RESULTS_DIR = Path(__file__).parent / "results"

# Rough per-case cost, measured from early runs: one agent turn with tool calls
# plus one judge call. Used only for the pre-run estimate.
ESTIMATED_COST_PER_CASE_USD = 0.045


def load_cases(suite: str | None, case_type: str | None, limit: int | None) -> list[dict]:
    paths = sorted(DATASET_DIR.glob(f"{suite}.jsonl" if suite else "*.jsonl"))
    if not paths:
        raise SystemExit(f"No dataset files matched suite={suite!r} in {DATASET_DIR}")

    cases: list[dict] = []
    for path in paths:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path.name} line {line_number}: {exc}") from exc

    if case_type:
        cases = [case for case in cases if case["case_type"] == case_type]

    return cases[:limit] if limit else cases


async def run_case(case: dict, *, use_judge: bool) -> CaseScore:
    """Run one case end to end and score it.

    Each case gets a fresh session id, so no case sees another's history and the
    order of the dataset cannot change the result.
    """
    session_id = f"eval_{uuid.uuid4().hex[:12]}"
    answer_parts: list[str] = []
    outcome: dict = {}
    answer = ""

    try:
        async for event in run_turn(
            session_id=session_id, user_message=case["message"], history=[]
        ):
            if event["type"] == "text":
                answer_parts.append(event["delta"])
            elif event["type"] == "done":
                # The done event carries the post-processed text, with invalid
                # citation markers already stripped. That is what a shopper
                # would have seen, so it is what gets graded.
                answer = event["text"]
                outcome = event["outcome"]
                break
    except Exception as exc:  # a crashed case must not abort the whole run
        return CaseScore(
            case_id=case["id"],
            case_type=case["case_type"],
            lang=case["lang"],
            error=f"{type(exc).__name__}: {exc}",
        )

    # A turn that errored mid-stream never emits `done`; fall back to whatever
    # text did arrive so the case is scored on real output rather than silence.
    if not answer:
        answer = "".join(answer_parts)

    score = score_case(case, outcome, answer)

    if use_judge and case.get("rubric"):
        judgement = await judge_answer(
            message=case["message"], rubric=case["rubric"], answer=answer
        )
        if judgement is not None:
            score.judge_score = judgement.score
            score.judge_reason = judgement.reason

    return score


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run the chatbot evaluation suite.")
    parser.add_argument("--suite", help="Dataset stem, e.g. policy, product, order, guardrail.")
    parser.add_argument("--case-type", help="Filter by case_type field.")
    parser.add_argument("--limit", type=int, help="Run only the first N cases.")
    parser.add_argument("--concurrency", type=int, default=3, help="Cases in flight at once.")
    parser.add_argument("--no-judge", action="store_true", help="Skip rubric grading.")
    parser.add_argument("--yes", action="store_true", help="Skip the cost confirmation.")
    parser.add_argument("--tag", default="", help="Label recorded with the results.")
    args = parser.parse_args()

    cases = load_cases(args.suite, args.case_type, args.limit)
    use_judge = not args.no_judge
    settings = get_settings()

    estimate = len(cases) * ESTIMATED_COST_PER_CASE_USD * (1.0 if use_judge else 0.6)
    print(f"Cases          : {len(cases)}")
    print(f"Agent model    : {settings.agent_model}")
    print(f"Judge          : {'on' if use_judge else 'off'}")
    print(f"Concurrency    : {args.concurrency}")
    print(f"Estimated cost : about ${estimate:.2f}")

    if not args.yes:
        if input("\nProceed? [y/N] ").strip().lower() not in {"y", "yes"}:
            print("Cancelled. Nothing was spent.")
            return 1

    try:
        await ping()
    except Exception as exc:
        print(f"\nCannot reach MongoDB: {exc}")
        print("Seed the database first: python -m seed.load")
        return 2

    # A semaphore rather than unbounded gather: the API rate limit is the
    # binding constraint, and firing 47 turns at once just produces 429s.
    semaphore = asyncio.Semaphore(args.concurrency)
    completed = 0

    async def worker(case: dict) -> CaseScore:
        nonlocal completed
        async with semaphore:
            score = await run_case(case, use_judge=use_judge)
            completed += 1
            marker = "!" if score.error else ("." if (score.judge_score or 5) >= 4 else "x")
            print(f"{marker} [{completed}/{len(cases)}] {score.case_id}", flush=True)
            return score

    print()
    started = datetime.now(timezone.utc)
    scores = await asyncio.gather(*(worker(case) for case in cases))
    finished = datetime.now(timezone.utc)

    summary = aggregate(list(scores))
    payload = {
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 1),
        "tag": args.tag,
        "agent_model": settings.agent_model,
        "classifier_model": settings.classifier_model,
        "embedding_mode": settings.embedding_mode,
        "judge_enabled": use_judge,
        "summary": summary,
        "cases": [asdict(score) for score in scores],
    }

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = started.strftime("%Y%m%d-%H%M%S")
    json_path = RESULTS_DIR / f"{stamp}.json"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    markdown = render_markdown(payload)
    markdown_path = RESULTS_DIR / f"{stamp}.md"
    markdown_path.write_text(markdown, encoding="utf-8")

    print(f"\n{markdown}")
    print(f"\nWrote {json_path}")
    print(f"Wrote {markdown_path}")

    await close_client()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
