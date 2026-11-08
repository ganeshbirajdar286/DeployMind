import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from ai_devops_agent.deployer.url_adapter import (
    extract_url_subpath,
    inspect_frontend_api_conventions,
    inspect_backend_route_prefixes,
    adapt_backend_url_for_frontend,
)
from ai_devops_agent.deployer.orchestrator import CloudDeployer
from ai_devops_agent.deployer.models import DeploymentResult
from ai_devops_agent.analyzer.models import ProjectAnalysis, ServiceMetadata, FrameworkType, Language, PackageManager


# ==========================================
# 1. URL Path Extraction Tests
# ==========================================
def test_extract_url_subpath():
    assert extract_url_subpath("http://localhost:3000/api") == "/api"
    assert extract_url_subpath("http://localhost:5000/api/v1") == "/api/v1"
    assert extract_url_subpath("http://localhost:8000/api/") == "/api"
    assert extract_url_subpath("https://my-backend.onrender.com/graphql") == "/graphql"
    assert extract_url_subpath("http://localhost:3000") == ""
    assert extract_url_subpath("http://localhost:3000/") == ""
    assert extract_url_subpath(None) == ""
    assert extract_url_subpath("") == ""


# ==========================================
# 2. Adaptation Logic Tests
# ==========================================
def test_adapt_preserves_subpath_from_user_env():
    # User originally entered 'http://localhost:3000/api'
    live_render_url = "https://portfolio-ldgk.onrender.com"
    orig_env = "http://localhost:3000/api"

    logs = []
    adapted = adapt_backend_url_for_frontend(
        live_backend_url=live_render_url,
        original_env_value=orig_env,
        env_key="VITE_BACKEND_URL",
        log_callback=logs.append,
    )

    assert adapted == "https://portfolio-ldgk.onrender.com/api"
    # Ensure detailed terminal telemetry was logged
    log_text = "\n".join(logs)
    assert "Intelligent Frontend API URL Adaptation" in log_text
    assert "VITE_BACKEND_URL" in log_text
    assert "Retained path suffix '/api'" in log_text


def test_adapt_preserves_nested_v1_subpath():
    live_render_url = "https://my-app.onrender.com"
    orig_env = "http://localhost:5000/api/v1"

    adapted = adapt_backend_url_for_frontend(
        live_backend_url=live_render_url,
        original_env_value=orig_env,
        env_key="NEXT_PUBLIC_API_URL",
    )

    assert adapted == "https://my-app.onrender.com/api/v1"


def test_adapt_avoids_duplicate_api_when_code_already_appends_api(tmp_path):
    # Setup mock frontend directory where code writes: `${VITE_BACKEND_URL}/api/users`
    fe_dir = tmp_path / "frontend"
    fe_dir.mkdir(parents=True)
    src_dir = fe_dir / "src"
    src_dir.mkdir()
    
    code_file = src_dir / "api.js"
    code_file.write_text(
        "const url = `${import.meta.env.VITE_BACKEND_URL}/api/users`;\nfetch(url);",
        encoding="utf-8",
    )

    live_render_url = "https://portfolio-ldgk.onrender.com"
    orig_env = "http://localhost:3000/api"

    adapted = adapt_backend_url_for_frontend(
        live_backend_url=live_render_url,
        original_env_value=orig_env,
        frontend_dir=fe_dir,
        env_key="VITE_BACKEND_URL",
    )

    # When code already appends '/api' explicitly, base URL should not have '/api' (avoids /api/api)
    assert adapted == "https://portfolio-ldgk.onrender.com"


def test_adapt_detects_backend_api_mount_when_frontend_makes_direct_calls(tmp_path):
    # Setup mock backend directory with: app.use('/api', routes)
    be_dir = tmp_path / "backend"
    be_dir.mkdir(parents=True)
    server_file = be_dir / "server.js"
    server_file.write_text(
        "const express = require('express');\nconst app = express();\napp.use('/api', router);",
        encoding="utf-8",
    )

    # Setup mock frontend directory with: axios.create({ baseURL: VITE_BACKEND_URL }) and direct calls
    fe_dir = tmp_path / "frontend"
    fe_dir.mkdir(parents=True)
    client_file = fe_dir / "client.ts"
    client_file.write_text(
        "import axios from 'axios';\nconst client = axios.create({ baseURL: import.meta.env.VITE_BACKEND_URL });\nclient.get('/projects');",
        encoding="utf-8",
    )

    live_render_url = "https://portfolio-ldgk.onrender.com"
    # User only provided root localhost without /api
    orig_env = "http://localhost:5000"

    adapted = adapt_backend_url_for_frontend(
        live_backend_url=live_render_url,
        original_env_value=orig_env,
        frontend_dir=fe_dir,
        backend_dir=be_dir,
        env_key="VITE_BACKEND_URL",
    )

    # Should detect that backend routes are mounted under '/api' and frontend client makes relative calls
    assert adapted == "https://portfolio-ldgk.onrender.com/api"


# ==========================================
# 3. CloudDeployer Integration Test
# ==========================================
def test_cloud_deployer_integrates_url_adapter_with_subpath(tmp_path):
    frontend_svc = ServiceMetadata(
        name="client",
        path="client",
        framework=FrameworkType.REACT_VITE,
        language=Language.JAVASCRIPT,
        package_manager=PackageManager.NPM,
        default_port=3000,
        env_vars_detected=["VITE_BACKEND_URL"],
    )
    backend_svc = ServiceMetadata(
        name="backend",
        path="backend",
        framework=FrameworkType.EXPRESS,
        language=Language.JAVASCRIPT,
        package_manager=PackageManager.NPM,
        default_port=5000,
        env_vars_detected=["PORT"],
    )

    analysis = ProjectAnalysis(
        repo_name="my-project",
        is_monorepo=True,
        services=[frontend_svc, backend_svc],
        suggested_deployment_targets={"client": "vercel", "backend": "render"},
    )

    deployer = CloudDeployer(vercel_token="mock_v_token", render_api_key="mock_r_token")

    with patch.object(deployer.render_deployer, "deploy_web_service") as mock_render, \
         patch.object(deployer.vercel_deployer, "deploy_project") as mock_vercel, \
         patch.object(deployer.render_deployer, "set_single_env_var"), \
         patch.object(deployer.render_deployer, "trigger_deploy"):

        mock_render.return_value = DeploymentResult(
            service_name="my-project-backend",
            target_platform="render",
            success=True,
            live_url="https://portfolio-ldgk.onrender.com",
            service_id="srv-backend-999",
            status="live",
        )

        mock_vercel.return_value = DeploymentResult(
            service_name="my-project-client",
            target_platform="vercel",
            success=True,
            live_url="https://my-project-client.vercel.app",
            status="READY",
        )

        summary = deployer.deploy_project(
            analysis=analysis,
            github_repo="test/my-project",
            branch="main",
            env_values={
                "VITE_BACKEND_URL": "http://localhost:3000/api",
            },
            workspace_dir=tmp_path,
        )

        assert summary.all_successful is True
        # Verify Vercel was invoked with the ADAPTED backend URL retaining '/api'
        mock_vercel.assert_called_once()
        called_vercel_env = mock_vercel.call_args[1]["env_vars"]
        assert called_vercel_env["VITE_BACKEND_URL"] == "https://portfolio-ldgk.onrender.com/api"
