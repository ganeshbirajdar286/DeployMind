# 🚀 AI DevOps Agent (`ai-devops-agent`)

Autonomous Cloud & Local DevOps Automation Platform with deterministic repository analysis, multi-service configuration generation, LangGraph-powered self-healing patches, and automated cloud deployments.

---

## 📦 Installation & Setup

### 🚀 Zero-Clone Install (Recommended for Any User)

Any user can install and run the application globally without cloning this repository:

```bash
# Option 1: Direct from GitHub main branch (Always latest release & features):
uv tool install --force "git+https://github.com/ganeshbirajdar286/DeployMind.git#subdirectory=services/agent-engine"

# Or using standard pip from Git:
pip install --upgrade "git+https://github.com/ganeshbirajdar286/DeployMind.git#subdirectory=services/agent-engine"

# Option 2: Pre-built Release Wheel (v0.1.2)
uv tool install --force https://github.com/ganeshbirajdar286/DeployMind/releases/download/v0.1.2/ai_devops_agent-0.1.2-py3-none-any.whl

# Or using pip with wheel:
pip install https://github.com/ganeshbirajdar286/DeployMind/releases/download/v0.1.2/ai_devops_agent-0.1.2-py3-none-any.whl
```

### 1. Install for Local Development

If you are developing locally inside this repository:

```bash
# Editable install for local development:
uv pip install -e .

# Or standard install:
pip install .
```

### 2. Building Distributions

To produce standalone `.whl` and `.tar.gz` packages:

```bash
uv build
```
Built artifacts are stored in `dist/`:
- `dist/ai_devops_agent-0.1.2-py3-none-any.whl` (Wheel)
- `dist/ai_devops_agent-0.1.2.tar.gz` (Source Distribution)

---

## 🛠️ CLI Commands

Once installed, the CLI is available globally via either `ai-devops` or `agent-engine`:

```bash
# Interactive Autonomous Pipeline (Prompt for target or enter GitHub URL / local path)
ai-devops

# Autonomous End-to-End Execution
ai-devops auto <github-url-or-local-path> --branch ai-devops-setup --max-retries 3

# Inspect a project repository
ai-devops inspect ./path/to/project

# Generate Docker & CI/CD bundles directly
ai-devops generate ./path/to/project

# Run LangGraph verification & patching loop
ai-devops run ./path/to/project --mode auto --write

# Deploy verified services (Frontend to Vercel, Backend to Render)
ai-devops deploy ./path/to/project --repo owner/repo
```

*(Note: `agent-engine` provides the exact same CLI arguments as an alias).*

---

## 🌐 FastAPI Microservice

To run the background DevOps microservice for API integrations:

```bash
# Using uvicorn:
uvicorn ai_devops_agent.main:app --host 0.0.0.0 --port 8000 --reload

# Or via backward-compatible shim:
python main.py
```

Endpoints:
- `GET /health` — Service healthcheck
- `POST /inspect` — Scans repository and returns frameworks, ports, and metadata
- `POST /run` — Launches LangGraph autonomous pipeline with sandbox verification
- `POST /deploy` — Triggers automated cloud deployment to Render / Vercel

---

## 🐍 Python Library Usage

You can also use `ai_devops_agent` directly inside your own Python applications:

```python
from ai_devops_agent import analyze_repository, generate_devops_bundle, run_devops_workflow

# 1. Analyze repository structure
analysis = analyze_repository("./my-project")
print(f"Detected {len(analysis.services)} service(s):")
for svc in analysis.services:
    print(f" - {svc.name} ({svc.framework.value}, Port: {svc.default_port})")

# 2. Generate DevOps configuration bundle
bundle = generate_devops_bundle(analysis)
for filepath, content in bundle.items():
    print(f"Generated: {filepath}")

# 3. Run full workflow with autonomous verification
result = run_devops_workflow(
    repo_path="./my-project",
    sandbox_mode="auto",
    max_retries=2,
)
```

---

## 🧪 Running Tests

To run the full test suite:

```bash
uv run --no-sync pytest
```
