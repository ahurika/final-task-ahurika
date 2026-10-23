# Rule: Tests and evidence

- Every implemented requirement must map to one or more tests or explicitly documented manual checks.
- Automated tests must not depend on uncontrolled randomness, network access, or real provider accounts.
- Fake providers should support deterministic modes (success, transient failure, permanent failure, timeout) and optionally seeded random failure rates.
- Record commands and actual outcomes for tests, load tests, and failure drills.
- Never manufacture a benchmark or claim an unrun test passed.
- Keep scripts in `load-tests/` and evidence/report files in `evidence/`.
- State environment limits and the first observed bottleneck in the load-test report.
