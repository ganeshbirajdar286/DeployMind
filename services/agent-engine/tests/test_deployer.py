import pytest
from unittest.mock import patch, MagicMock
from ai_devops_agent.deployer.models import DeploymentResult, MultiServiceDeploymentSummary
from ai_devops_agent.deployer.render import RenderDeployer
from ai_devops_agent.deployer.vercel import VercelDeployer
from ai_devops_agent.deployer.orchestrator import CloudDeployer, partition_service_env_vars
from ai_devops_agent.analyzer.models import ProjectAnalysis, ServiceMetadata, FrameworkType, Language, PackageManager


def test_render_deployer_without_key():
    deployer = RenderDeployer(api_key=None)
    ok, owner_id, msg = deployer.verify_credentials()
    assert ok is False
    assert "No RENDER_API_KEY" in msg


def test_vercel_deployer_without_token():
    deployer = VercelDeployer(token=None)
    ok, user, msg = deployer.verify_credentials()
    assert ok is False
    assert "No VERCEL_TOKEN" in msg


@patch("requests.get")
def test_render_verify_credentials(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [{"owner": {"id": "usr-test-123", "name": "Jane DevOps"}}]
    mock_get.return_value = mock_resp

    deployer = RenderDeployer(api_key="rnd_testkey")
    ok, owner_id, msg = deployer.verify_credentials()
    assert ok is True
    assert owner_id == "usr-test-123"
    assert "Jane DevOps" in msg


@patch("requests.get")
def test_vercel_verify_credentials(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"user": {"id": "usr-vercel-456", "username": "janedevops"}}
    mock_get.return_value = mock_resp

    deployer = VercelDeployer(token="ver_testtoken")
    ok, user, msg = deployer.verify_credentials()
    assert ok is True
    assert user == "janedevops"


def test_partition_service_env_vars():
    frontend_svc = ServiceMetadata(
        name="client",
        path="client",
        framework=FrameworkType.REACT_VITE,
        language=Language.JAVASCRIPT,
        package_manager=PackageManager.NPM,
        default_port=3000,
        env_vars_detected=["VITE_BASE_URL", "VITE_BAKEND_URL"],
    )
    backend_svc = ServiceMetadata(
        name="backend",
        path="backend",
        framework=FrameworkType.EXPRESS,
        language=Language.JAVASCRIPT,
        package_manager=PackageManager.NPM,
        default_port=5000,
        env_vars_detected=["MOGODB_URL", "JWT_SECRET", "PORT", "FRONTEND_URL"],
    )

    all_env = {
        "MOGODB_URL": "mongodb+srv://user:pass@cluster0.mongodb.net",
        "JWT_SECRET": "mysecretkey",
        "PORT": "5000",
        "FRONTEND_URL": "http://localhost:3000",
        "VITE_BASE_URL": "http://localhost:5000",
        "VITE_BAKEND_URL": "http://localhost:5000",
        "BREVO_API_KEY": "brevo_secret_123",
    }

    # Frontend must ONLY receive frontend-prefixed variables (NO MOGODB_URL or JWT_SECRET)
    fe_env = partition_service_env_vars(frontend_svc, all_env, is_frontend=True)
    assert "VITE_BASE_URL" in fe_env
    assert "VITE_BAKEND_URL" in fe_env
    assert "MOGODB_URL" not in fe_env
    assert "JWT_SECRET" not in fe_env
    assert "BREVO_API_KEY" not in fe_env
    assert "PORT" not in fe_env

    # Backend must receive backend variables and DB secrets
    be_env = partition_service_env_vars(backend_svc, all_env, is_frontend=False)
    assert be_env["MOGODB_URL"] == "mongodb+srv://user:pass@cluster0.mongodb.net"
    assert be_env["JWT_SECRET"] == "mysecretkey"
    assert be_env["BREVO_API_KEY"] == "brevo_secret_123"
    assert be_env["FRONTEND_URL"] == "http://localhost:3000"
    assert "VITE_BASE_URL" not in be_env
    assert "VITE_BAKEND_URL" not in be_env


def test_cloud_deployer_orchestration_and_bidirectional_url_sync():
    frontend_svc = ServiceMetadata(
        name="client",
        path="client",
        framework=FrameworkType.REACT_VITE,
        language=Language.JAVASCRIPT,
        package_manager=PackageManager.NPM,
        default_port=3000,
        env_vars_detected=["VITE_BASE_URL", "VITE_BAKEND_URL"],
    )
    backend_svc = ServiceMetadata(
        name="backend",
        path="backend",
        framework=FrameworkType.EXPRESS,
        language=Language.JAVASCRIPT,
        package_manager=PackageManager.NPM,
        default_port=5000,
        env_vars_detected=["MOGODB_URL", "JWT_SECRET", "FRONTEND_URL"],
    )

    analysis = ProjectAnalysis(
        repo_name="3w-test1",
        is_monorepo=True,
        services=[frontend_svc, backend_svc],
        suggested_deployment_targets={"client": "vercel", "backend": "render"},
    )

    deployer = CloudDeployer(vercel_token="mock_v_token", render_api_key="mock_r_token")

    with patch.object(deployer.render_deployer, "deploy_web_service") as mock_render, \
         patch.object(deployer.vercel_deployer, "deploy_project") as mock_vercel, \
         patch.object(deployer.render_deployer, "set_single_env_var") as mock_set_env, \
         patch.object(deployer.render_deployer, "trigger_deploy") as mock_trigger:

        mock_render.return_value = DeploymentResult(
            service_name="3w-test1-backend",
            target_platform="render",
            success=True,
            live_url="https://3w-test1-backend.onrender.com",
            dashboard_url="https://dashboard.render.com/web/srv-backend123",
            service_id="srv-backend123",
            status="live",
        )

        mock_vercel.return_value = DeploymentResult(
            service_name="3w-test1-client",
            target_platform="vercel",
            success=True,
            live_url="https://3w-test1-client.vercel.app",
            dashboard_url="https://vercel.com/user/3w-test1-client",
            status="READY",
        )

        summary = deployer.deploy_project(
            analysis=analysis,
            github_repo="ganeshbirajdar286/3w-test1",
            branch="ai-devops-setup",
            env_values={
                "MOGODB_URL": "mongodb://sample",
                "JWT_SECRET": "secret",
                "FRONTEND_URL": "http://localhost:3000",
                "VITE_BASE_URL": "http://localhost:5000",
                "VITE_BAKEND_URL": "http://localhost:5000",
            },
        )

        assert summary.all_successful is True
        assert summary.live_backend_url == "https://3w-test1-backend.onrender.com"
        assert summary.live_frontend_url == "https://3w-test1-client.vercel.app"

        # 1. Backend called first with backend env only
        mock_render.assert_called_once()
        called_render_env = mock_render.call_args[1]["env_vars"]
        assert called_render_env["MOGODB_URL"] == "mongodb://sample"
        assert called_render_env["JWT_SECRET"] == "secret"
        assert called_render_env["PORT"] == "5000"
        assert "VITE_BASE_URL" not in called_render_env

        # 2. Vercel called second with backend live URL injected into VITE_BASE_URL & VITE_BAKEND_URL
        mock_vercel.assert_called_once()
        called_vercel_env = mock_vercel.call_args[1]["env_vars"]
        assert called_vercel_env["VITE_BASE_URL"] == "https://3w-test1-backend.onrender.com"
        assert called_vercel_env["VITE_BAKEND_URL"] == "https://3w-test1-backend.onrender.com"
        assert "MOGODB_URL" not in called_vercel_env
        assert "JWT_SECRET" not in called_vercel_env

        # 3. Render updated with live Vercel frontend URL & redeploy triggered
        mock_set_env.assert_called_with("srv-backend123", "FRONTEND_URL", "https://3w-test1-client.vercel.app")
        mock_trigger.assert_called_with("srv-backend123")


def test_cloud_deployer_aborts_when_backend_fails():
    frontend_svc = ServiceMetadata(
        name="client",
        path="client",
        framework=FrameworkType.REACT_VITE,
        language=Language.JAVASCRIPT,
        package_manager=PackageManager.NPM,
        default_port=3000,
    )
    backend_svc = ServiceMetadata(
        name="backend",
        path="backend",
        framework=FrameworkType.EXPRESS,
        language=Language.JAVASCRIPT,
        package_manager=PackageManager.NPM,
        default_port=5000,
    )

    analysis = ProjectAnalysis(
        repo_name="3w-test1",
        is_monorepo=True,
        services=[frontend_svc, backend_svc],
        suggested_deployment_targets={"client": "vercel", "backend": "render"},
    )

    deployer = CloudDeployer(vercel_token="mock_v_token", render_api_key="mock_r_token")

    with patch.object(deployer.render_deployer, "deploy_web_service") as mock_render, \
         patch.object(deployer.vercel_deployer, "deploy_project") as mock_vercel:

        # Backend fails on Render
        mock_render.return_value = DeploymentResult(
            service_name="3w-test1-backend",
            target_platform="render",
            success=False,
            live_url=None,
            status="build_failed",
            error_message="Build failed on Render",
        )

        summary = deployer.deploy_project(
            analysis=analysis,
            github_repo="ganeshbirajdar286/3w-test1",
            branch="main",
            env_values={"PORT": "5000"},
        )

        # Backend was called, but frontend was NEVER deployed because backend failed!
        mock_render.assert_called_once()
        mock_vercel.assert_not_called()
        assert summary.all_successful is False
        assert summary.frontend_result is None
        assert summary.backend_result.success is False



@patch("requests.put")
@patch("requests.post")
def test_render_deployer_methods(mock_post, mock_put):
    mock_put.return_value = MagicMock(status_code=200)
    mock_post.return_value = MagicMock(status_code=200, json=lambda: {"id": "dep-123"})

    deployer = RenderDeployer(api_key="rnd_test")
    
    # Test sync_all_env_vars
    sync_ok = deployer.sync_all_env_vars("srv-123", {"PORT": "5000", "DB": "postgres"})
    assert sync_ok is True
    assert mock_put.call_args[0][0] == "https://api.render.com/v1/services/srv-123/env-vars"

    # Test set_single_env_var
    set_ok = deployer.set_single_env_var("srv-123", "FRONTEND_URL", "https://app.vercel.app")
    assert set_ok is True
    assert mock_put.call_args[0][0] == "https://api.render.com/v1/services/srv-123/env-vars/FRONTEND_URL"

    # Test trigger_deploy
    dep_id = deployer.trigger_deploy("srv-123")
    assert dep_id == "dep-123"
    assert mock_post.call_args[0][0] == "https://api.render.com/v1/services/srv-123/deploys"


def test_vercel_production_domain_resolution():
    deployer = VercelDeployer(token="mock_token")

    # 1. Resolves clean domain from aliases containing both preview hash and production domain
    aliases = [
        "tmp29z606x1-client-ncz7pfqcx-ganeshbirajdar286s-projects.vercel.app",
        "tmp29z606x1-client.vercel.app",
    ]
    domain = deployer.get_production_domain("tmp29z606x1-client", aliases=aliases)
    assert domain == "https://tmp29z606x1-client.vercel.app"

    # 2. Canonical fallback without aliases
    domain_fallback = deployer.get_production_domain("tmp29z606x1-client")
    assert domain_fallback == "https://tmp29z606x1-client.vercel.app"

