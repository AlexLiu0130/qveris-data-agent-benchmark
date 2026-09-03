# Project rules

- Keep three benchmark suites, with 100 cases per suite.
- Each runtime evaluation uses one agent only; Search and Inspect are not allowed.
- Each case permits one structured output and one public `get` execution.
- Evaluate exactly four benchmark metrics: semantic accuracy, data accuracy, end-to-end latency, and token usage. Define their calculations before adding scoring code.
- Never commit credentials, tokens, raw responses, raw result artifacts, or other secrets.
- Do not deploy to production without separate, explicit authorization.
- Keep changes minimal. Do not choose a runtime stack or add scaffolding until it is needed.
