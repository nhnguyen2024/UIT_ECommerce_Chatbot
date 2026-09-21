# Evaluation results

What the 56-case evaluation measured, run by run, and what each change did.
The method (suites, metrics, scoring rules) is in
[`architecture.md`](architecture.md) §10. Raw per-run output is written to
`backend/evals/results/` and is not committed; the numbers below are copied from
those reports.

All runs: agent and classifier `gpt-5-mini` on Azure OpenAI (Japan East),
`AGENT_EFFORT=medium`, Atlas Automated Embedding (`voyage-4`), concurrency 3,
judge enabled. Every case runs in a fresh session.

## Summary

| Metric | First run | Final run 1 | Final run 2 |
|---|---|---|---|
| Intent routing accuracy | 91.1% | 96.4% | 94.6% |
| Policy recall@3 | 92.5% | 97.5% | 87.5% |
| Policy recall@5 | 97.5% | 97.5% | 87.5% |
| Mean reciprocal rank | 0.938 | 0.967 | 0.867 |
| Tool selection | 82.4% | 100% | 100% |
| Answer quality, judge mean (1–5) | 3.98 | 4.89 | 4.80 |
| Answer quality pass rate (≥ 4) | 66.1% | 98.2% | 94.6% |
| Groundedness | 100% | 100% | 100% |
| Invalid citation rate | 3.6% | 1.8% | 1.8% |
| Security pass rate (10 cases) | 100% | 100% | 100% |
| Errors | 0 | 0 | 0 |
| Average latency per turn | 11.5 s | 12.8 s | 12.8 s |
| Cost of the full run | 0.111 USD | 0.131 USD | 0.121 USD |

**Headline for the report:** on the final code, answer quality passes on 94.6 to
98.2% of cases across two identical runs, with groundedness, security and tool
selection at 100% and no errors in either. Quote the range, not the better run.

The first run's answer-quality figure is not comparable with the later ones on
its own: part of the gain is the judge calibration described next, measured
separately so the two effects are not confused.

## 1. First run, and a mis-specified judge

The first full run passed 66% of cases on answer quality, yet groundedness was
100%. Reading the judge's reasons showed why: it gave 2/5 to replies containing
any figure the rubric did not mention, such as a ticket number, the hotline, a
tracking code or a correct product specification. The judge prompt said "a
figure the rubric did not specify scores 2 or lower". It was meant to catch
contradicted figures, and the model applied it literally.

Groundedness is already checked deterministically against tool output, so the
rule was narrowed to figures that **contradict** the rubric. To separate that
change from any change in the assistant, the first run's saved answers were
re-graded with the corrected judge, without re-running the assistant:

| Same answers | Judge mean | Pass rate |
|---|---|---|
| Original judge | 3.98 | 66.1% |
| Corrected judge | 4.73 | 91.1% |

Every case still failing after re-grading was a genuine fault, which is the
evidence that the correction removed noise rather than lowering the bar. One
rubric (`grd-en-11`) was also narrowed: its "no timeline" meant a refund
timeline, not the support team's published response time.

## 2. Faults the evaluation found, and the fixes

| Fault | Cases | Fix |
|---|---|---|
| A jailbreak attempt returned "assistant unavailable": Azure's Prompt Shields rejects it with HTTP 400 before the model runs | `grd-inj-05` | A content-filter rejection is now a refusal: a polite decline in the shopper's language, and the turn is labelled an injection attempt |
| An English complaint was answered in Vietnamese | `grd-en-11` | The language the classifier detects is stated at the end of the system prompt on every turn |
| Clothing and footwear requests routed as product questions | `grd-vi-04`, `grd-vi-13`, `grd-en-14` | The classifier is told which categories the store sells |
| A vague product request got only clarifying questions | `prd-vi-03`, `prd-vi-05`, `prd-en-10` | Search first with what the shopper gave; ask at most one narrowing question after |
| "Same warranty on Lazada?" answered "cannot confirm" | `pol-vi-20` | The warranty policy now states its periods apply on every channel (the fact existed only in the return policy's marketplace section) |
| Card refund timing not found | `pol-en-04` | The model had filtered the search to the payment policy; refund timing is in the return policy. The filter's description now says so |
| The out-of-scope reply never said what the store sells | `grd-en-14` | It now names the categories |

**A regression, caught by the next run.** The first classifier fix added an
example about OTPs, after which the classifier began labelling ordinary phone
numbers and emails as sensitive credentials, and order lookups got the
credential warning instead of an answer (run B: 8 order cases failed). The rule
was made explicit: only OTPs, CVVs, card numbers and passwords are credentials;
a phone number or email given to look up an order is how the store verifies
one. Checked on the classifier alone before re-running: only the two genuine
credential cases were flagged, and intent accuracy was 98%.

## 3. All runs

| Run | Change since previous | Intent | R@5 | Tools | Judge mean | Pass | Grounded | Cost |
|---|---|---|---|---|---|---|---|---|
| A | Baseline | 91.1% | 97.5% | 82.4% | 3.98 | 66.1% | 100% | 0.111 |
| A′ | Same answers, corrected judge | – | – | – | 4.73 | 91.1% | – | ~0.03 |
| B | Classifier, prompt and policy fixes | 96.4% | 87.5% | 82.4% | 4.43 | 82.1% | 100% | 0.111 |
| C | Credential rule fixed (run 1 of 2) | 96.4% | 87.5% | 100% | 4.79 | 94.6% | 100% | 0.124 |
| D | Same code (run 2 of 2) | 98.2% | 82.5% | 100% | 4.77 | 92.9% | 98.2% | 0.130 |
| E | Search filter and out-of-scope reply (final, run 1) | 96.4% | 97.5% | 100% | 4.89 | 98.2% | 100% | 0.131 |
| F | Same code (final, run 2) | 94.6% | 87.5% | 100% | 4.80 | 94.6% | 100% | 0.121 |

Runs C/D and E/F repeat identical code to show run-to-run variation. It is
real: the model phrases its search queries differently each time, and C and D
differ by five points of recall on the same code. Differences of that size
between single runs should not be read as improvements or regressions.

## 4. What remains

- **Policy retrieval varies between runs** (recall@5 97.5% and 87.5% on the same
  code). In run F, `pol-vi-05` (who pays return shipping) and `pol-vi-12` (the
  30-day replacement window) missed the right section because of how the model
  worded its search query. Averaging several runs, or a reranker over the fused
  results, would reduce the variance; neither is done.
- **`prd-en-04`** fails on the judge, not the assistant. The rubric warns against
  claiming built-in GPS for a watch that uses the phone's; all three watches
  recommended do list "Built-in GPS" in the catalogue, but the judge cannot see
  the catalogue and marks the claim down. Grounding is measured separately and
  deterministically for this reason.
- **Invalid citations (1 to 2 cases per run).** After a failed verification, the
  model sometimes cites the order it could not open. The citation guardrail
  removes the marker before the shopper sees it; the metric records that it
  happened.
- **Latency** averages 11 to 18 s per turn, mostly reasoning at
  `AGENT_EFFORT=medium` plus two model calls per turn (classifier and agent).
  Lowering the effort is the obvious experiment, to be judged on this dataset.
- **Single-turn only.** Multi-turn behaviour, such as following up on a product
  already discussed, is not measured.
