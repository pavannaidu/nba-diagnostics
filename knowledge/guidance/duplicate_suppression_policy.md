# Duplicate Suppression Policy

This document defines deterministic guardrails for the synthetic dog diagnostic recommendation workspace.

## Policy intent
- Do not recommend repeating the same diagnostic when it was completed recently and the current intake does not establish a new clinical question.
- Recent-test recency is a hard guardrail, not a soft ranking factor.

## Expected behavior
- If the same test falls inside its suppression window, the recommendation workflow should mark it as suppressed.
- Suppressed diagnostics can still appear in the explanation, but only as "not selected because recently completed."
- The final recommendation should prefer the next supported action rather than surfacing a suppressed duplicate as the lead recommendation.

## Explanation pattern
- State the prior recency in days when available.
- State that the diagnostic was not selected because the same test was performed within the recent-test window.
- Keep the explanation factual and avoid suggesting that the duplicate suppression is optional.
