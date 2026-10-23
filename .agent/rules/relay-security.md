# Rule: Security and privacy

- Never commit real credentials or `.env` files.
- Keep `.env.example` to variable names and clearly fake placeholder values.
- Validate and constrain request payloads.
- Enforce tenant scoping in all notification and preference reads/writes.
- Use synthetic test recipients and content.
- Do not log secrets, authorization headers, or full notification bodies by default.
- Keep local/test authentication clearly distinguished from production-grade authentication.
- Explain security limitations in the design and README.
