# AGENTS

Guidance for AI when working in this Python repository.

## Toolchain

- **Package/dependency management:** `uv` exclusively. Do not use `pip install`, `poetry`, or raw `venv` commands.
  - Add a dependency: `uv add <package>`
  - Add a dev dependency: `uv add --dev <package>`
  - Sync the environment: `uv sync`
  - Run a script/command in the project env: `uv run <command>`
- **Formatting & linting:** `ruff` for both formatting and linting.
  - Format: `uv run ruff format .`
  - Lint: `uv run ruff check .`
  - Lint with autofix: `uv run ruff check --fix .`
- **Testing:** `pytest`.
  - Run: `uv run pytest`
  - Run a single test: `uv run pytest path/to/test_file.py::test_name`
- **Target Python version:** >=3.13. Use modern syntax freely (e.g. `match` statements, `X | Y` unions, `type` statement for aliases, new generics syntax).

## When to run checks

Only run lint, type-check, or test commands when explicitly asked to, or when finishing a task that clearly calls for verification (e.g. "make sure this works"). Don't run them proactively after every small edit — it's noisy and slows iteration. Do mention if you suspect something is broken, even if you don't run the check yourself.

## Code style

- **Comments:** minimal. Prefer self-documenting code — clear names, small functions, obvious structure — over explanatory comments. Add a comment only when the code can't explain itself (a non-obvious workaround, a tricky invariant, a reference to an external constraint). No docstrings purely for the sake of having them; if a function's purpose is clear from its name and signature, skip the docstring.
- **Type hints:** pragmatic, not strict.
  - Annotate function signatures on public/exported functions and methods (params + return type).
  - Internal helpers, local variables, and private functions can skip annotations when types are obvious from context.
  - Avoid `Any` where a real type is easy to express, but don't contort code to eliminate every last one.
  - Use built-in generics (`list[str]`, `dict[str, int]`) and `X | None` instead of `typing.List`, `typing.Optional`, etc.
- **Imports:** absolute imports within the package; group stdlib / third-party / local with a blank line between (ruff's isort handles this — don't hand-arrange).
- **Errors:** raise specific exceptions, not bare `Exception`. Prefer custom exception classes for domain errors over stringly-typed error handling.
- **Functions:** prefer small, composable functions over large ones. Avoid deep nesting — return early instead of wrapping logic in nested conditionals.

## Testing conventions

- Tests live under `tests/`, mirroring the source package structure.
- Use `pytest` fixtures and plain `assert` statements — no `unittest`-style `self.assertEqual`.
- Name tests descriptively: `test_<function>_<scenario>_<expected_result>` when it aids clarity, but don't force a rigid template if a simpler name reads fine.
- New non-trivial logic should come with tests, but don't pad coverage with trivial tests for trivial code (e.g. simple property accessors).

## Git

AI should not run git commands (commit, push, branch, merge, rebase, etc.) unless explicitly asked to in a given session. Assume the user manages version control themselves. It's fine to suggest a commit message when a logical chunk of work is done, but don't act on it unprompted.

## General working style

- When making changes, prefer minimal, targeted diffs over broad rewrites unless a rewrite is requested or clearly necessary.
- If a task is ambiguous, ask a clarifying question rather than guessing on something consequential (architecture, public API shape, dependency choice).
- Don't add dependencies speculatively — only add what the current task actually needs.
- Surface tradeoffs briefly when there's a meaningful design choice (e.g. "this could be a dataclass or a plain dict — I went with a dataclass for the validation, let me know if you'd rather keep it simple").