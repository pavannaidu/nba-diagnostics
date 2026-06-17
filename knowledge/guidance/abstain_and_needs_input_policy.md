# Abstain And Needs-Input Policy

This document defines when the synthetic dog diagnostic recommendation workflow should avoid forcing a next test.

## Needs-input policy
- Return `needs_input` when the intake is missing the basic visit anchor: patient, as-of time, presenting complaint, or severity.
- Return `needs_input` when there is no meaningful clinical context from either structured symptom flags or note text.

## Abstain policy
- Abstain when evidence is too weak to support a specific additional diagnostic action.
- Abstain when duplicate suppression removes the most obvious test and the remaining options are weakly justified.
- Abstain when the evidence is split and no action is supported by both patient-specific facts and broader guidance.

## Explanation pattern
- State clearly that no additional diagnostic is recommended right now.
- Explain whether abstention came from insufficient evidence, recent duplicate testing, or lack of a new clinical question.
- If more information would unblock the decision, name the missing context succinctly.
