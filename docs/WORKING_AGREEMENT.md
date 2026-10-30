# Working agreement

- Read `docs/PROJECT_SPEC.md` before starting. Work on one stage at a time and stop at its gate.
- Write tests first. Never edit existing tests or anything under `tests_hidden/` unless told to.
- Make small commits with clear messages. Run the full test suite before saying a task is done, and show the output.
- Do not invent APIs, library versions or file paths. Check the actual docs or code.
- Never commit secrets. Use environment variables and `.env.example`.
- Label anything not implemented as `# STUB`, and list stubs at the end of each stage.
- When something is ambiguous or conflicts with the spec, ask instead of guessing.
- Keep LLM and embedding providers behind the interfaces. Do not hard-code model names.
- After each stage, update `docs/DECISIONS.md`: what works, what is stubbed, what broke, what you chose and why.
- Never claim a feature works without evidence (test output or a run log).
- Everything in this project is simulated. Say so wherever it applies. Describe OpsLoop as
  "autonomous investigation and fix proposal, human-gated release", never "fully autonomous".

## Commands

```bash
pip install -e ".[dev]"               # install
docker compose up -d --build --wait   # start stack
pytest -v                             # all tests (integration tests need Postgres up)
ruff check . && ruff format --check . && mypy
```
