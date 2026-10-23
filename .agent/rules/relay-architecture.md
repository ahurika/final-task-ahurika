# Rule: Architecture and scope

- Read `PRD.md` and the relevant sections of `DESIGN.md` before changing architecture.
- The design document is the source of truth after user review; if it conflicts with the PRD, stop and explain the conflict.
- Keep API acceptance separate from asynchronous delivery.
- Keep channel-specific behavior behind adapters/interfaces.
- Do not introduce a broker, cache, ORM, framework, or cloud service without explaining the need and trade-off.
- Keep the prototype small enough for the user to run and explain locally.
- Any architecture change must identify affected FR/NFR IDs, tests, and documentation.
