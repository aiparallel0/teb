# Contributing to teb

Thank you for your interest in contributing to teb! This guide will help you get started.

## Development Setup

```bash
git clone https://github.com/aiparallel0/teb.git
cd teb
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
pip install -r requirements.txt
cp .env.example .env        # Edit with your settings
```

Or use the one-liner: `bash start.sh`

### Running the Application

```bash
uvicorn teb.main:asgi_app --reload
```

### Running Tests

```bash
pytest tests/ -v
```

All 601 tests should pass. Tests use an in-memory SQLite database and mock all external services.

## How to Contribute

### Reporting Bugs

- Use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md)
- Include steps to reproduce, expected vs actual behavior, and your environment

### Suggesting Features

- Use the [feature request template](.github/ISSUE_TEMPLATE/feature_request.md)
- Explain the problem you're solving and why existing features don't cover it

### Submitting Code

1. **Fork** the repository
2. **Create a branch** from `main`: `git checkout -b feature/your-feature`
3. **Make your changes** — keep them focused and minimal
4. **Add tests** for new functionality
5. **Run the full test suite**: `pytest tests/ -v`
6. **Commit** with a clear message: `git commit -m "Add feature X"`
7. **Push** and open a pull request

### Pull Request Guidelines

- Reference any related issues
- Describe what changed and why
- Ensure all tests pass
- Keep changes focused — one feature or fix per PR

## Code Style

- Follow [PEP 8](https://peps.python.org/pep-0008/) for Python code
- Use type hints for function signatures
- Keep functions small and focused
- Use docstrings for public functions and classes
- Match the existing code style in the file you're editing

## Architecture

| Module | Purpose |
|---|---|
| `main.py` | FastAPI endpoints and request handling |
| `models.py` | Dataclass definitions for all domain objects |
| `storage.py` | SQLite data access layer (27 tables) |
| `decomposer.py` | Goal → task decomposition (10 templates + AI) |
| `executor.py` | API-based task execution |
| `browser.py` | Browser automation (Playwright) |
| `agents.py` | Multi-agent delegation system |
| `integrations.py` | Pre-built service catalog |
| `payments.py` | Payment provider integration (Mercury + Stripe) |
| `discovery.py` | Tool/service discovery engine |
| `deployer.py` | Deployment engine (Vercel, Railway, Render) |
| `provisioning.py` | Service auto-signup via browser automation |
| `messaging.py` | Telegram + webhook notifications |
| `ai_client.py` | Unified AI client (Anthropic + OpenAI) |
| `auth.py` | JWT authentication + RBAC |
| `security.py` | SSRF-safe URL validation for outbound HTTP |
| `config.py` | Environment configuration |

## Questions?

Open an issue with the "question" label. We're happy to help!
