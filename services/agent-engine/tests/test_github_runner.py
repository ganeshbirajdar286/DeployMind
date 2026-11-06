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
    assert "push:" in workflow_content
    assert "ai-devops-setup" in workflow_content


def test_workflow_yaml_syntax_validity_with_multiple_secrets():
    import yaml
    from generator.engine import get_jinja_env

    env = get_jinja_env()
    tmpl = env.get_template("github/ai_devops_verify.yml.j2")
    sample_vars = [
        "BREVO_API_KEY",
        "BREVO_VERIFIED_SENDER",
        "FRONTEND_URL",
        "MY_PERSONAL_EMAIL",
        "VITE_BACKEND_URL",
    ]
    rendered = tmpl.render(
        branch_name="ai-devops-setup",
        default_port="3000",
        health_check_path="/",
        all_env_vars=sample_vars,
    )

    # Validate that PyYAML safely parses the entire rendered document
    parsed = yaml.safe_load(rendered)
    assert parsed["name"] == "AI DevOps Agent Verification Sandbox"
    steps = parsed["jobs"]["verify-sandbox"]["steps"]
    prep_step = next(s for s in steps if s.get("name") == "Prepare Sandbox Environment")
    assert "env" in prep_step
    assert prep_step["env"]["BREVO_API_KEY"] == "${{ secrets.BREVO_API_KEY }}"
    assert prep_step["env"]["BREVO_VERIFIED_SENDER"] == "${{ secrets.BREVO_VERIFIED_SENDER }}"
    assert "run" in prep_step


def test_git_pusher_pr_and_merge_flow():
    from unittest.mock import patch, MagicMock
    from runner.git_pusher import GitPusher

    pusher = GitPusher(token="ghp_test_token_123")

    # 1. Test get_default_branch
    with patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {"default_branch": "main"})
        branch = pusher.get_default_branch("test/repo")
        assert branch == "main"

    # 2. Test create_pull_request
    with patch("requests.get") as mock_get, patch("requests.post") as mock_post:
        # No existing open PR
        mock_get.return_value = MagicMock(status_code=200, json=lambda: [])
        # Successful create PR
        mock_post.return_value = MagicMock(
            status_code=201,
            json=lambda: {"html_url": "https://github.com/test/repo/pull/1", "number": 1},
        )
        ok, pr_url, pr_num, msg = pusher.create_pull_request("test/repo", "ai-devops-setup", "main")
        assert ok is True
        assert pr_url == "https://github.com/test/repo/pull/1"
        assert pr_num == 1

    # 3. Test is_pull_request_merged
    with patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=204)
        is_merged = pusher.is_pull_request_merged("test/repo", 1)
        assert is_merged is True

    # 4. Test merge_pull_request
    with patch("requests.put") as mock_put:
        mock_put.return_value = MagicMock(status_code=200)
        merge_ok, merge_msg = pusher.merge_pull_request("test/repo", 1)
        assert merge_ok is True
        assert "Successfully merged PR #1" in merge_msg

