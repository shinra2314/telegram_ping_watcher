# pyproject.toml + uv Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `pyproject.toml` so the project can be installed with `uv`, while keeping `requirements.txt` intact for Docker and Windows users.

**Architecture:** Add `pyproject.toml` with project metadata and all current pinned deps mirrored from `requirements.txt`. Keep `requirements.txt` as-is — Docker, `run_local.ps1`, and README all reference it. Update `CLAUDE.md` to mention `uv` as an alternative.

**Tech Stack:** Python packaging (PEP 517), `uv` (fast Python package manager), existing `requirements.txt` stays as the source of truth for pinned versions.

---

### Task 1: Create pyproject.toml

**Files:**
- Create: `pyproject.toml`

- [ ] **Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=70"]
build-backend = "setuptools.backends.legacy:build"

[project]
name = "pulse-desk"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "aiosqlite==0.22.1",
    "annotated-doc==0.0.4",
    "annotated-types==0.7.0",
    "anyio==4.13.0",
    "certifi==2026.4.22",
    "click==8.3.3",
    "colorama==0.4.6",
    "fastapi==0.136.1",
    "h11==0.16.0",
    "httpcore==1.0.9",
    "httpx==0.28.1",
    "idna==3.15",
    "pyaes==1.6.1",
    "pyasn1==0.6.3",
    "pydantic==2.13.4",
    "pydantic-settings==2.14.1",
    "pydantic_core==2.46.4",
    "python-dotenv==1.2.2",
    "rsa==4.9.1",
    "starlette==1.0.0",
    "Telethon==1.43.2",
    "typing-inspection==0.4.2",
    "typing_extensions==4.15.0",
    "uvicorn==0.46.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "httpx>=0.28",
]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Verify pyproject.toml parses**

```bash
python -c "import tomllib; tomllib.loads(open('pyproject.toml').read()); print('OK')"
```

Expected output: `OK`

- [ ] **Step 3: Update CLAUDE.md — add uv alternative in Commands section**

In `CLAUDE.md`, replace the "Run the app:" block with:

```markdown
**Run the app:**
```powershell
# Windows (auto-setup venv, install deps, start)
.\run_local.ps1
.\run_local.ps1 -SkipInstall   # skip pip install

# With uv (faster, cross-platform)
uv sync
python main.py

# Manual
.\.venv\Scripts\python.exe main.py
```

**Install dev dependencies:**
```bash
uv sync --extra dev
```
```

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml CLAUDE.md
git commit -m "feat: add pyproject.toml with uv support (requirements.txt unchanged)"
```
