from pathlib import Path
from runner.docker_runner import DockerRunner
from agent.graph import run_devops_workflow
from agent.patcher import diagnose_and_patch

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_docker_runner_validation():
    runner = DockerRunner()
    target_repo = FIXTURES_DIR / "sample-react-vite"

    # Create dummy Dockerfile for runner test
    dockerfile_path = target_repo / "Dockerfile"
    dockerfile_path.write_text("FROM nginx:alpine\nEXPOSE 80\nCMD [\"nginx\", \"-g\", \"daemon off;\"]\n")

    try:
        result = runner.build_image(context_path=target_repo, dockerfile_path=dockerfile_path)
        assert result.success is True
        assert result.exit_code == 0
    finally:
        if dockerfile_path.exists():
            dockerfile_path.unlink()


def test_langgraph_workflow_sample_fastapi():
    target = FIXTURES_DIR / "sample-fastapi"
    state = run_devops_workflow(repo_path=str(target), max_retries=2)

    assert state["current_stage"] == "SUCCESS"
    assert state["build_succeeded"] is True
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
    assert len(patch.diagnosis) > 0
    assert len(patch.patch_explanation) > 0
