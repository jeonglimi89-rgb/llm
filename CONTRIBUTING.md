# Contributing to vLLM Orchestrator

Thanks for considering a contribution! This guide covers setup, testing, and PR conventions.

## Setup

```bash
git clone https://github.com/jeonglimi89-rgb/llm
cd llm

# Python 3.12+ venv
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate

pip install -r vllm_orchestrator/requirements.txt
pip install ruff pytest pytest-cov cryptography
```

## Running tests

```bash
# Fast regression (no external deps, CI gate)
cd vllm_orchestrator
pytest tests/regression/ -v

# Chaos (requires running orchestrator at localhost:8100)
python tests/chaos/chaos_test.py --scenario all

# Load (requires running stack)
python tests/load_test.py --scenario mixed --concurrent 5 --total 20

# Coverage
pytest tests/regression/ --cov=src/app --cov-report=term-missing
```

## Linting

```bash
ruff check vllm_orchestrator/src/ vllm_orchestrator/tests/
ruff check --fix ...      # auto-fix
```

## Running locally (with Docker)

```bash
./deploy.sh local            # basic stack
./deploy.sh local-tls        # + nginx + TLS
./deploy.sh local-ha         # + HA (2 orch instances)
```

## Development workflow

1. **Branch** from `main`: `git checkout -b feat/my-feature`
2. **Code + test** — keep `tests/regression/` green
3. **Commit messages** — conventional style:
   - `feat: add X` / `fix: handle Y` / `docs: ...` / `refactor: ...` / `test: ...` / `chore: ...`
4. **Pull request** — CI must pass (lint + regression + Docker build + security scan)
5. **Review** — 1 approval required
6. **Merge** — squash or rebase (no merge commits on main)

## Scope of contributions

**Welcome**:
- Bug fixes (especially regression coverage)
- New task types (in `domain/registry.py`)
- New LLM adapters (`llm/adapters/`)
- Observability improvements (metrics/traces)
- Prompt engineering for domain-specific tasks
- Documentation

**Requires discussion first** (open an issue):
- Breaking API changes
- New external dependencies
- Schema changes
- Model routing logic changes

## Release process

Maintainers:

```bash
# 1. Update CHANGELOG.md
# 2. Tag
git tag -a v0.2.0 -m "v0.2.0 — summary"
git push origin v0.2.0

# → release.yml workflow:
#   - Builds multi-arch image
#   - Pushes to ghcr.io/jeonglimi89-rgb/vllm-orchestrator:v0.2.0 (+ :latest, :0.2, :0)
#   - Creates GitHub Release with auto-generated notes
```

## Code of Conduct

Be respectful. Report issues to maintainers. See GitHub's [Community Guidelines](https://docs.github.com/en/site-policy/github-terms/github-community-guidelines).
