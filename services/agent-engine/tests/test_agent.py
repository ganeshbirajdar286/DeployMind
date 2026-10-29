import tempfile
from pathlib import Path
from runner.docker_runner import DockerRunner
from agent.graph import run_devops_workflow
from agent.patcher import diagnose_and_patch, patcher_app

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_docker_runner_validation():
    runner = DockerRunner()
    target_repo = FIXTURES_DIR / "sample-react-vite"

    dockerfile_path = target_repo / "Dockerfile"
    dockerfile_path.write_text("FROM nginx:alpine\nEXPOSE 80\nCMD [\"nginx\", \"-g\", \"daemon off;\"]\n")

    try:
        result = runner.build_image(context_path=target_repo, dockerfile_path=dockerfile_path)
        assert result.success is True
        assert result.exit_code == 0
    finally:
        if dockerfile_path.exists():
            dockerfile_path.unlink()


def test_docker_runner_compose_validation():
    runner = DockerRunner()
    sample_compose = """version: '3.8'
services:
  web:
    build: .
    ports:
      - "3000:3000"
"""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_compose = Path(temp_dir) / "docker-compose.yml"
        temp_compose.write_text(sample_compose, encoding="utf-8")

        res = runner.validate_compose(compose_file_path=temp_compose, ports_to_probe=[3000])
        assert res.success is True
        assert res.syntax_valid is True


def test_langgraph_workflow_sample_fastapi():
    target = FIXTURES_DIR / "sample-fastapi"
    state = run_devops_workflow(repo_path=str(target), max_retries=2)

    assert state["current_stage"] == "SUCCESS"
    assert state["build_succeeded"] is True
    assert state["compose_succeeded"] is True
    assert "Dockerfile" in state["artifacts"]
    assert "docker-compose.yml" in state["artifacts"]
    assert ".github/workflows/ci.yml" in state["artifacts"]


def test_ai_patcher_rule_fallback():
    broken_dockerfile = "FROM node:20-alpine\nWORKDIR /app\nCOPY package*.json ./\nRUN npm ci\nCMD [\"npm\", \"start\"]\n"
    error_tail = "npm error code ETARGET\nnpm error notarget No matching version found for foo@^9.9.9\nnpm ci can only install with existing package-lock"

    patch = diagnose_and_patch(
        current_dockerfile=broken_dockerfile,
        error_tail=error_tail,
        framework="react_vite",
    )

    assert "npm install" in patch.updated_dockerfile
    assert "npm ci" not in patch.updated_dockerfile
    assert patch.failure_category == "DEPENDENCY_MISMATCH"
    assert len(patch.diagnosis) > 0
    assert len(patch.patch_explanation) > 0


def test_ai_patcher_missing_python_module():
    dockerfile = "FROM python:3.11-slim\nWORKDIR /app\nRUN pip install -r requirements.txt\nCMD [\"python\", \"main.py\"]\n"
    error_tail = "Traceback (most recent call last):\n  File \"/app/main.py\", line 2, in <module>\n    import yt_dlp\nModuleNotFoundError: No module named 'yt_dlp'\n"

    patch = diagnose_and_patch(dockerfile, error_tail, framework="fastapi")

    assert patch.failure_category == "MISSING_PYTHON_MODULE"
    assert "RUN pip install --no-cache-dir yt-dlp" in patch.updated_dockerfile
    assert "yt_dlp" in patch.diagnosis


def test_patcher_subgraph_langgraph_execution():
    """Directly verifies the LangGraph patcher subgraph state machine."""
    broken_dockerfile = "FROM python:3.11-slim\nWORKDIR /app\nCOPY . .\nCMD [\"python\", \"main.py\"]\n"
    error_tail = "error: command 'gcc' failed: No such file or directory. Python.h: No such file."

    initial_state = {
        "current_dockerfile": broken_dockerfile,
        "error_tail": error_tail,
        "framework": "fastapi",
        "failure_category": "GENERAL",
        "missing_module": None,
        "diagnosis": "",
        "patch_explanation": "",
        "updated_dockerfile": broken_dockerfile,
        "is_syntax_valid": False,
        "validation_errors": [],
        "internal_retries": 0,
        "max_internal_retries": 2,
    }

    final_state = patcher_app.invoke(initial_state)

    assert final_state["failure_category"] == "MISSING_HEADER"
    assert "build-essential" in final_state["updated_dockerfile"]
    assert final_state["is_syntax_valid"] is True
    assert final_state["internal_retries"] >= 1


def test_ai_patcher_missing_whisper_and_ffmpeg():
    dockerfile = """FROM python:3.11-slim AS builder
WORKDIR /app
RUN pip install -r requirements.txt
FROM python:3.11-slim AS runner
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
CMD ["python", "main.py"]
"""
    error_tail = "ModuleNotFoundError: No module named 'whisper'"
    patch = diagnose_and_patch(dockerfile, error_tail, framework="fastapi")

    assert patch.failure_category == "MISSING_PYTHON_MODULE"
    assert "openai-whisper" in patch.updated_dockerfile
    assert "ffmpeg" in patch.updated_dockerfile
