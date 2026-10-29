from pathlib import Path
from runner.github_runner import GitHubActionsRunner
from analyzer.detector import analyze_repository
from generator.engine import generate_devops_bundle

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_github_runner_fallback_without_token():
    runner = GitHubActionsRunner(token=None)
    result = runner.dispatch_and_wait(
        repo="octocat/Hello-World",
        branch="main",
        health_port=8080,
    )

    assert result.success is True
    assert result.syntax_valid is True
    assert result.is_simulated is True
    assert "No GITHUB_TOKEN" in result.logs[2]


def test_generator_emits_cloud_sandbox_workflow():
    target = FIXTURES_DIR / "sample-fastapi"
    analysis = analyze_repository(target)
    bundle = generate_devops_bundle(analysis)

    assert ".github/workflows/ai-devops-verify.yml" in bundle
    workflow_content = bundle[".github/workflows/ai-devops-verify.yml"]

    assert "name: AI DevOps Agent Verification Sandbox" in workflow_content
    assert "workflow_dispatch:" in workflow_content
    assert "docker compose up -d --build" in workflow_content
    assert "docker compose down -v" in workflow_content
    assert "curl" in workflow_content
