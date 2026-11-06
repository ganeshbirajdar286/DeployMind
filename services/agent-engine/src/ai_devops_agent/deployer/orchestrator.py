import os
import getpass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Callable
from ai_devops_agent.analyzer.models import ProjectAnalysis, ServiceMetadata, FrameworkType
from .models import DeploymentResult, MultiServiceDeploymentSummary
from .render import RenderDeployer
from .vercel import VercelDeployer
from ai_devops_agent.utils.theme import (
    box,
    print_box,
    CYAN,
    PINK,
    PURPLE,
    EMERALD,
    AMBER,
    RED,
    WHITE,
    MUTED,
    BOLD,
    DIM,
    RESET,
)


def partition_service_env_vars(
    service: ServiceMetadata,
    all_env_values: Dict[str, str],
    is_frontend: bool,
) -> Dict[str, str]:
    """Partitions environment variables so Frontend only gets frontend vars (e.g. VITE_*) and Backend gets backend vars (e.g. DB, secrets)."""
    service_env: Dict[str, str] = {}
    detected_for_service = set(service.env_vars_detected or [])

    frontend_prefixes = ("VITE_", "NEXT_PUBLIC_", "REACT_APP_", "VUE_APP_", "GATSBY_", "NUXT_PUBLIC_")
    backend_sensitive_keywords = (
        "MOGODB", "MONGO", "DATABASE", "DB_", "POSTGRES", "MYSQL",
        "REDIS", "JWT", "SECRET", "PASSWORD", "PWD", "PORT",
        "BREVO", "SENDGRID", "MAIL", "SMTP", "PRIVATE"
    )

    if is_frontend:
        for k, v in all_env_values.items():
            has_frontend_prefix = any(k.startswith(p) for p in frontend_prefixes)
            is_backend_secret = any(kw in k.upper() for kw in backend_sensitive_keywords)

            # Only include if has frontend prefix OR detected in frontend and NOT a backend database/auth secret
            if has_frontend_prefix:
                service_env[k] = v
            elif k in detected_for_service and not is_backend_secret and k != "PORT" and k != "FRONTEND_URL":
                service_env[k] = v
    else:
        # Backend gets all backend-detected variables and any variables not prefixed with frontend prefixes
        for k, v in all_env_values.items():
            has_frontend_prefix = any(k.startswith(p) for p in frontend_prefixes)
            if not has_frontend_prefix:
                service_env[k] = v
            elif k in detected_for_service:
                service_env[k] = v

    return service_env


class CloudDeployer:
    """Orchestrates end-to-end multi-service deployment: Backend -> Render, Frontend -> Vercel with dynamic URL inter-linking."""

    def __init__(
        self,
        vercel_token: Optional[str] = None,
        render_api_key: Optional[str] = None,
    ):
        self.vercel_token = vercel_token or os.getenv("VERCEL_TOKEN")
        self.render_api_key = render_api_key or os.getenv("RENDER_API_KEY")

        self.vercel_deployer = VercelDeployer(token=self.vercel_token)
        self.render_deployer = RenderDeployer(api_key=self.render_api_key)

    def deploy_project(
        self,
        analysis: ProjectAnalysis,
        github_repo: str,
        branch: str = "ai-devops-setup",
        env_values: Optional[Dict[str, str]] = None,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> MultiServiceDeploymentSummary:
        """Deploys all detected services with bidirectional dynamic URL synchronization."""
        def emit(msg: str):
            if log_callback:
                log_callback(msg)

        deploy_lines = [
            f"{MUTED}Repository: {RESET} {BOLD}{WHITE}{github_repo}{RESET}",
            f"{MUTED}Branch:     {RESET} {PINK}{branch}{RESET}",
            f"{MUTED}Services:   {RESET} {len(analysis.services)} service(s) configured",
            "",
            f"{CYAN}Deployment Targets:{RESET}",
            *[
                f"  {MUTED}•{RESET} {BOLD}{s.name}{RESET} {MUTED}({s.framework.value}){RESET} -> {PURPLE}{analysis.suggested_deployment_targets.get(s.name, 'render').upper()}{RESET}"
                for s in analysis.services
            ],
        ]
        emit("\n" + box("🚀 Autonomous Cloud Deployment Orchestration", deploy_lines, border_color=CYAN) + "\n")

        env_values = dict(env_values or {})
        repo_url = f"https://github.com/{github_repo}"

        # Group services by target platform
        backend_service = None
        frontend_service = None

        for s in analysis.services:
            target = analysis.suggested_deployment_targets.get(s.name, "render").lower()
            if target == "vercel":
                frontend_service = s
            else:
                backend_service = s

        # If not cleanly split, fall back to first as backend or frontend
        if not frontend_service and not backend_service and analysis.services:
            backend_service = analysis.services[0]

        backend_res: Optional[DeploymentResult] = None
        frontend_res: Optional[DeploymentResult] = None
        all_results: List[DeploymentResult] = []

        # ==========================================
        # 1. Partition Environment Variables
        # ==========================================
        backend_env: Dict[str, str] = {}
        frontend_env: Dict[str, str] = {}

        if backend_service:
            backend_env = partition_service_env_vars(backend_service, env_values, is_frontend=False)
            backend_env["PORT"] = str(backend_service.default_port)

        if frontend_service:
            frontend_env = partition_service_env_vars(frontend_service, env_values, is_frontend=True)

        # ==========================================
        # 2. Deploy Backend to Render First
        # ==========================================
        if backend_service:
            emit(f"[Step 1/3] Deploying Backend Service '{backend_service.name}' to Render...")
            emit(f"  -> Configured Backend environment variables ({len(backend_env)}): {', '.join(sorted(backend_env.keys()))}")

            backend_res = self.render_deployer.deploy_web_service(
                repo_url=repo_url,
                branch=branch,
                service_name=f"{analysis.repo_name}-{backend_service.name}",
                root_dir=backend_service.path,
                env_vars=backend_env,
                port=backend_service.default_port,
                is_docker=True,
                log_callback=emit,
            )
            all_results.append(backend_res)
            if backend_res.success and backend_res.live_url:
                emit(f"  [SUCCESS] Backend live at: {backend_res.live_url}\n")
            else:
                emit(f"\n❌ [ABORTED] Backend service '{backend_service.name}' failed to deploy on Render (Status: {backend_res.status}).")
                emit(f"   Frontend deployment CANNOT proceed until the backend is live and healthy.")
                if backend_res.error_message:
                    emit(f"   Error: {backend_res.error_message}")
                emit(f"   Inspect Render logs: {backend_res.dashboard_url or 'https://dashboard.render.com'}\n")
                summary = MultiServiceDeploymentSummary(
                    repo_name=analysis.repo_name,
                    branch_name=branch,
                    all_successful=False,
                    frontend_result=None,
                    backend_result=backend_res,
                    details=all_results,
                )
                display_deployment_summary(summary)
                return summary

        # ==========================================
        # 3. Update Frontend with Live Backend URL & Deploy to Vercel
        # ==========================================
        if frontend_service:
            emit(f"[Step 2/3] Deploying Frontend Service '{frontend_service.name}' to Vercel...")

            # Automatically link Frontend to the newly created Render Backend URL!
            if backend_res and backend_res.live_url:
                backend_url_keys_updated = []
                backend_key_patterns = ("BACKEND", "BAKEND", "BASE_URL", "API_URL", "SERVER_URL", "API_ENDPOINT")
                for k in list(frontend_env.keys()) + list(frontend_service.env_vars_detected):
                    if any(pat in k.upper() for pat in backend_key_patterns):
                        frontend_env[k] = backend_res.live_url
                        backend_url_keys_updated.append(k)

                if not backend_url_keys_updated:
                    frontend_env["VITE_BACKEND_URL"] = backend_res.live_url
                    frontend_env["VITE_BASE_URL"] = backend_res.live_url
                    frontend_env["NEXT_PUBLIC_API_URL"] = backend_res.live_url
                    backend_url_keys_updated.extend(["VITE_BACKEND_URL", "VITE_BASE_URL"])

                emit(f"  -> Injected Live Backend URL ({backend_res.live_url}) into Frontend: {', '.join(sorted(set(backend_url_keys_updated)))}")

            emit(f"  -> Configured Frontend environment variables ({len(frontend_env)}): {', '.join(sorted(frontend_env.keys()))}")

            frontend_res = self.vercel_deployer.deploy_project(
                repo_slug=github_repo,
                branch=branch,
                service_name=f"{analysis.repo_name}-{frontend_service.name}",
                root_dir=frontend_service.path,
                framework=frontend_service.framework.value,
                env_vars=frontend_env,
                log_callback=emit,
            )
            all_results.append(frontend_res)
            if frontend_res.success and frontend_res.live_url:
                emit(f"  [SUCCESS] Frontend live at: {frontend_res.live_url}\n")
            else:
                emit(f"  [WARNING] Frontend deployment status: {frontend_res.status}\n")

        # ==========================================
        # 4. Synchronize Live Frontend URL Back to Render Backend
        # ==========================================
        if backend_service and backend_res and backend_res.service_id and frontend_res and frontend_res.live_url:
            emit(f"[Step 3/3] Synchronizing Live Frontend URL ({frontend_res.live_url}) back into Render Backend...")
            frontend_patterns = ("FRONTEND", "CLIENT", "CORS", "ORIGIN", "APP_URL", "WEB_URL", "SITE_URL")
            target_frontend_keys = []

            for k in list(backend_env.keys()) + list(backend_service.env_vars_detected):
                if any(pat in k.upper() for pat in frontend_patterns):
                    target_frontend_keys.append(k)

            if not target_frontend_keys:
                target_frontend_keys = ["FRONTEND_URL"]

            for k in sorted(set(target_frontend_keys)):
                emit(f"  -> Updating Render environment variable '{k}' = {frontend_res.live_url} ...")
                ok_set = self.render_deployer.set_single_env_var(backend_res.service_id, k, frontend_res.live_url)
                if ok_set:
                    emit(f"  [OK] Successfully updated Render '{k}' to '{frontend_res.live_url}'!")

            emit("  -> Triggering Render redeploy to activate updated Frontend URL...")
            self.render_deployer.trigger_deploy(backend_res.service_id)
            emit("  [OK] Render redeploy dispatched with live Frontend URL.\n")

        all_ok = all(r.success for r in all_results if r)

        summary = MultiServiceDeploymentSummary(
            repo_name=analysis.repo_name,
            branch_name=branch,
            all_successful=all_ok,
            frontend_result=frontend_res,
            backend_result=backend_res,
            live_frontend_url=frontend_res.live_url if frontend_res else None,
            live_backend_url=backend_res.live_url if backend_res else None,
            details=all_results,
        )

        display_deployment_summary(summary)
        return summary


def display_deployment_summary(summary: MultiServiceDeploymentSummary):
    """Displays celebratory, formatted production deployment URLs in a styled rounded box."""
    lines = [
        f"{MUTED}Repository:{RESET} {BOLD}{WHITE}{summary.repo_name}{RESET}  {MUTED}(Branch: {PINK}{summary.branch_name}{MUTED}){RESET}",
    ]

    if summary.frontend_result and summary.frontend_result.live_url:
        lines.extend([
            "",
            f"{CYAN}🌐 [Frontend — Vercel Production]{RESET}",
            f"   {MUTED}Production URL:{RESET}  {BOLD}{WHITE}{summary.frontend_result.live_url}{RESET}",
            f"   {MUTED}Dashboard:     {RESET}  {DIM}{summary.frontend_result.dashboard_url or 'https://vercel.com'}{RESET}",
        ])
    elif summary.frontend_result and not summary.frontend_result.success:
        lines.extend([
            "",
            f"{RED}❌ [Frontend — Vercel Failed]{RESET}",
            f"   {MUTED}Error:{RESET} {summary.frontend_result.error_message or summary.frontend_result.status}",
        ])

    if summary.backend_result and summary.backend_result.live_url:
        lines.extend([
            "",
            f"{PURPLE}⚙️  [Backend — Render Web Service]{RESET}",
            f"   {MUTED}API URL:       {RESET}  {BOLD}{WHITE}{summary.backend_result.live_url}{RESET}",
            f"   {MUTED}Dashboard:     {RESET}  {DIM}{summary.backend_result.dashboard_url or 'https://dashboard.render.com'}{RESET}",
        ])
    elif summary.backend_result and not summary.backend_result.success:
        lines.extend([
            "",
            f"{RED}❌ [Backend — Render Failed]{RESET}",
            f"   {MUTED}Error:{RESET} {summary.backend_result.error_message or summary.backend_result.status}",
        ])

    if summary.all_successful:
        lines.extend([
            "",
            f"{EMERALD}✔ Bidirectional URL linking active: Frontend CORS & API URLs linked!{RESET}",
        ])
        print("\n" + box("🎉 PRODUCTION DEPLOYMENT COMPLETE — ALL SERVICES LIVE!", lines, border_color=EMERALD) + "\n")
    else:
        print("\n" + box("⚠️  PRODUCTION DEPLOYMENT SUMMARY", lines, border_color=AMBER) + "\n")


def interactive_cloud_deployment(
    analysis: ProjectAnalysis,
    detected_repo: str,
    branch_name: str = "ai-devops-setup",
    local_env_dir: Optional[Path] = None,
    default_env_values: Optional[Dict[str, str]] = None,
) -> Optional[MultiServiceDeploymentSummary]:
    """Interactively prompts user for credentials and triggers Vercel + Render deployment with bidirectional URL linking."""
    print("\n" + "=" * 60)
    print("☁️ Cloud Deployment Configuration")
    print("=" * 60)
    print("Deploy targets:")
    print("  * Frontend -> Vercel")
    print("  * Backend  -> Render")
    print("=" * 60)

    # 1. Credentials
    vercel_token = os.getenv("VERCEL_TOKEN")
    if not vercel_token:
        try:
            vercel_token = getpass.getpass("Enter VERCEL_TOKEN (or press Enter to skip Vercel): ").strip()
        except Exception:
            vercel_token = input("Enter VERCEL_TOKEN (or press Enter to skip Vercel): ").strip()

    render_token = os.getenv("RENDER_API_KEY")
    if not render_token:
        try:
            render_token = getpass.getpass("Enter RENDER_API_KEY (or press Enter to skip Render): ").strip()
        except Exception:
            render_token = input("Enter RENDER_API_KEY (or press Enter to skip Render): ").strip()

    if not vercel_token and not render_token:
        print("\n[Notice] No Vercel or Render credentials provided. Skipping automated cloud deployment.")
        print("To deploy manually:")
        print("  - Vercel: Import repo on https://vercel.com/new")
        print("  - Render: Create web service on https://dashboard.render.com/web/new\n")
        return None

    # 2. Collect Environment Variables
    all_vars = []
    for s in analysis.services:
        all_vars.extend(s.env_vars_detected)
    all_vars = sorted(list(set(all_vars)))

    env_values = dict(default_env_values or {})

    # Check local .env files (root and subdirectories) for pre-fill
    if local_env_dir:
        search_paths = [
            local_env_dir / ".env",
            local_env_dir / ".env.local",
            local_env_dir / ".env.production",
        ]
        try:
            for child in Path(local_env_dir).iterdir():
                if child.is_dir() and not child.name.startswith("."):
                    search_paths.append(child / ".env")
        except Exception:
            pass

        for env_file in search_paths:
            if env_file.exists():
                try:
                    for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip("'\"")
                            if v and k not in env_values:
                                env_values[k] = v
                except Exception:
                    pass

    missing_vars = [v for v in all_vars if v not in env_values]
    if missing_vars:
        print(f"\nFound {len(missing_vars)} unconfigured environment variable(s) for production:")
        for v in missing_vars:
            try:
                val = input(f"  Enter value for '{v}' (press Enter to skip): ").strip()
                if val:
                    env_values[v] = val
            except Exception:
                pass

    deployer = CloudDeployer(vercel_token=vercel_token, render_api_key=render_token)
    return deployer.deploy_project(
        analysis=analysis,
        github_repo=detected_repo,
        branch=branch_name,
        env_values=env_values,
        log_callback=print,
    )
