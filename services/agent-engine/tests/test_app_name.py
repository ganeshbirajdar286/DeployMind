import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from ai_devops_agent.utils.sanitizer import sanitize_app_name, derive_default_app_name
from ai_devops_agent.analyzer.detector import analyze_repository
from ai_devops_agent.analyzer.models import ProjectAnalysis, ServiceMetadata, FrameworkType, Language, PackageManager
from ai_devops_agent.deployer.orchestrator import CloudDeployer
from ai_devops_agent.deployer.models import DeploymentResult
from ai_devops_agent.cli import prompt_for_app_name


def test_sanitize_app_name():
    assert sanitize_app_name("my-app") == "my-app"
    assert sanitize_app_name("My Portfolio App") == "my-portfolio-app"
    assert sanitize_app_name("Cool_Project!#$123") == "cool-project-123"
    assert sanitize_app_name("foo---bar___baz") == "foo-bar-baz"
    assert sanitize_app_name("-my-app-") == "my-app"
    assert sanitize_app_name("") == ""
    assert len(sanitize_app_name("a" * 120)) == 80


def test_derive_default_app_name():
    assert derive_default_app_name("https://github.com/ganeshbirajdar286/3w-test1") == "3w-test1"
    assert derive_default_app_name("https://github.com/owner/my-cool-app.git") == "my-cool-app"
    assert derive_default_app_name("ganeshbirajdar286/portfolio") == "portfolio"
    
    # Avoids temporary folders when repo is provided
    assert derive_default_app_name("C:/Temp/tmpv85pzgsk", repo="ganeshbirajdar286/3w-test1") == "3w-test1"
    assert derive_default_app_name("", repo="ganeshbirajdar286/3w-test1") == "3w-test1"


def test_analyze_repository_with_custom_project_name(tmp_path):
    # Create a minimal node project in tmp_path
    (tmp_path / "package.json").write_text('{"name": "temp-name", "scripts": {"start": "node index.js"}}')
    (tmp_path / "index.js").write_text("console.log('hi');")

    # Analyze with explicit project_name override
    analysis = analyze_repository(tmp_path, project_name="my-custom-portal")
    assert analysis.repo_name == "my-custom-portal"
    assert analysis.services[0].name == "my-custom-portal"


@patch("ai_devops_agent.deployer.orchestrator.VercelDeployer")
@patch("ai_devops_agent.deployer.orchestrator.RenderDeployer")
def test_cloud_deployer_app_name_override(mock_render_cls, mock_vercel_cls):
    mock_render = MagicMock()
    mock_vercel = MagicMock()
    mock_render_cls.return_value = mock_render
    mock_vercel_cls.return_value = mock_vercel

    mock_render.deploy_web_service.return_value = DeploymentResult(
        service_name="portfolio-server",
        target_platform="render",
        success=True,
        live_url="https://portfolio-server.onrender.com",
    )
    mock_vercel.deploy_project.return_value = DeploymentResult(
        service_name="portfolio-client",
        target_platform="vercel",
        success=True,
        live_url="https://portfolio-client.vercel.app",
    )

    # Initial analysis created from a temp directory (like tmpv85pzgsk)
    analysis = ProjectAnalysis(
        repo_name="tmpv85pzgsk",
        is_monorepo=True,
        services=[
            ServiceMetadata(
                name="server",
                path="server",
                framework=FrameworkType.FASTAPI,
                language=Language.PYTHON,
                package_manager=PackageManager.PIP,
                default_port=8000,
            ),
            ServiceMetadata(
                name="client",
                path="client",
                framework=FrameworkType.REACT_VITE,
                language=Language.JAVASCRIPT,
                package_manager=PackageManager.NPM,
                default_port=5173,
            ),
        ],
        suggested_deployment_targets={"server": "render", "client": "vercel"},
    )

    deployer = CloudDeployer(vercel_token="v_tok", render_api_key="r_key")
    summary = deployer.deploy_project(
        analysis=analysis,
        github_repo="ganeshbirajdar286/3w-test1",
        app_name="portfolio",
    )

    # Verify Render was called with clean app name "portfolio-server", not "tmpv85pzgsk-server"
    render_args = mock_render.deploy_web_service.call_args[1]
    assert render_args["service_name"] == "portfolio-server"

    # Verify Vercel was called with clean app name "portfolio-client", not "tmpv85pzgsk-client"
    vercel_args = mock_vercel.deploy_project.call_args[1]
    assert vercel_args["service_name"] == "portfolio-client"

    assert summary.all_successful is True


@patch("builtins.input")
def test_prompt_for_app_name(mock_input):
    # When user enters nothing (presses Enter), default is retained
    mock_input.return_value = ""
    assert prompt_for_app_name("default-project") == "default-project"

    # When user enters a custom name with spaces and capitals, it is sanitized
    mock_input.return_value = "My Awesome App"
    assert prompt_for_app_name("default-project") == "my-awesome-app"
