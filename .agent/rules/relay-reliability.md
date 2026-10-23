# Rule: Delivery reliability

- Apply durable idempotency at the database/transaction boundary.
- Do not rely only on process memory to detect duplicate requests or jobs.
- Acknowledge a request as accepted only after the required durable state is committed.
- Classify failures as transient or permanent.
- Use bounded exponential backoff with a maximum attempt count and a documented terminal state.
- Avoid retry storms; document provider outage behavior and evaluate a circuit breaker.
- If a database and queue are separate systems, address the dual-write problem explicitly.
- State the boundary of duplicate prevention honestly: external provider timeouts can leave delivery outcome ambiguous.
- Test duplicate requests, concurrent duplicate requests where practical, worker restart, and provider timeout behavior.
