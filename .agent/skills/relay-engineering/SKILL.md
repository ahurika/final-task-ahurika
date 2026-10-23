# Skill: Relay Engineering Workflow

## Purpose
Guide design-first development and evidence collection for the Relay notification-platform prototype.

## Before every task
1. Read `AGENTS.md`, `PRD.md`, `DESIGN.md`, and the relevant learning-log entries.
2. State the phase and requirement IDs in scope.
3. Check whether the relevant design section is complete and approved.
4. If a required decision is missing, ask one focused question or present a small set of options; do not silently invent a decision.

## Design phase
- Challenge assumptions and identify failure cases.
- Keep calculations reproducible.
- Ensure API, data model, architecture, state machine, and retry rules agree.
- Compare alternatives and record why the chosen option is proportionate to the prototype.
- Do not begin implementation until the user approves the completed design.

## Implementation phase
- Implement one vertical slice at a time.
- Keep channel providers behind an adapter interface.
- Make provider outcomes deterministic in tests.
- Use durable idempotency and transaction-safe state changes.
- Add tests before claiming behavior is correct.
- Comment on at least three genuinely implemented design patterns.

## Verification phase
- Run the relevant tests and inspect output.
- Report command, environment, actual result, failure, and limitation.
- For load tests, report achieved rate, latency percentiles if available, error rate, duration, and first bottleneck.
- For failure drills, describe exactly what was deliberately broken and what actually happened.
- Save reproducible evidence without secrets or personal data.

## Completion criteria
The task is not complete merely because code exists. The design, tests, load-test report, failure-drill report, walkthrough/video details, mock-interview notes, and final submission links must be accounted for.
