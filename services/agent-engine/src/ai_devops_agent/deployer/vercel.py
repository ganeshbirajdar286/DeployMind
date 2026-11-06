import os
import time
from typing import Dict, List, Optional, Tuple, Callable
import requests
from .models import DeploymentResult


class VercelDeployer:
    """Manages deployments of frontend web applications to Vercel via REST API."""

    def __init__(self, token: Optional[str] = None):
        self.token = token or os.getenv("VERCEL_TOKEN")
        self.base_url = "https://api.vercel.com"

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def verify_credentials(self) -> Tuple[bool, Optional[str], str]:
        """Verifies Vercel access token and retrieves current username."""
        if not self.token:
            return False, None, "No VERCEL_TOKEN provided."
        try:
            resp = requests.get(f"{self.base_url}/v2/user", headers=self._headers(), timeout=15)
            if resp.status_code == 200:
                user = resp.json().get("user", {})
                username = user.get("username") or user.get("email")
                return True, username, f"Authenticated as Vercel user '{username}'."
            return False, None, f"Vercel authentication failed (HTTP {resp.status_code}): {resp.text}"
        except Exception as e:
            return False, None, f"Error connecting to Vercel API: {e}"

    def get_github_repo_id(self, repo_slug: str) -> Optional[int]:
        """Fetches the numerical GitHub repository ID needed for Vercel gitSource."""
        clean_slug = repo_slug.replace("https://github.com/", "").rstrip("/").replace(".git", "")
        headers = {"User-Agent": "AI-DevOps-Agent/1.0"}
        gh_token = os.getenv("GITHUB_TOKEN")
        if gh_token:
            headers["Authorization"] = f"Bearer {gh_token}"
        try:
            resp = requests.get(f"https://api.github.com/repos/{clean_slug}", headers=headers, timeout=10)
            if resp.status_code == 200:
                return resp.json().get("id")
        except Exception:
            pass
        return None

    def get_production_domain(self, project_name: str, aliases: Optional[List[str]] = None) -> str:
        """Determines the canonical production domain name (e.g. 'https://my-app.vercel.app') instead of the per-deployment hash URL."""
        # 1. Check aliases returned from the deployment
        if aliases and isinstance(aliases, list):
            target_canonical = f"{project_name.lower()}.vercel.app"
            for a in aliases:
                if a.lower() == target_canonical:
                    return f"https://{a}"
            # Filter out preview/branch hash URLs containing '-projects-' or '-git-'
            clean = [
                a for a in aliases
                if not any(sub in a.lower() for sub in ["-git-", "-projects-"])
            ]
            if clean:
                return f"https://{clean[0]}"
            if aliases:
                return f"https://{aliases[0]}"

        # 2. Check project domains configured on Vercel
        try:
            resp = requests.get(f"{self.base_url}/v9/projects/{project_name}/domains", headers=self._headers(), timeout=10)
            if resp.status_code == 200:
                dom_data = resp.json().get("domains", [])
                if dom_data and isinstance(dom_data, list):
                    for d in dom_data:
                        name = d.get("name")
                        if name and not d.get("redirect"):
                            return f"https://{name}"
                    if dom_data[0].get("name"):
                        return f"https://{dom_data[0].get('name')}"
        except Exception:
            pass

        # 3. Canonical default Vercel domain
        return f"https://{project_name.lower()}.vercel.app"

    def deploy_project(
        self,
        repo_slug: str,
        branch: str,
        service_name: str,
        root_dir: str = "frontend",
        framework: str = "vite",
        env_vars: Optional[Dict[str, str]] = None,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> DeploymentResult:
        """Links repository to Vercel project, injects environment variables, and deploys branch."""
        def emit(msg: str):
            if log_callback:
                log_callback(msg)

        emit(f"[VercelDeployer] Preparing deployment for '{service_name}' on Vercel...")
        ok, username, msg = self.verify_credentials()
        if not ok:
            emit(f"  [ERROR] {msg}")
            return DeploymentResult(
                service_name=service_name,
                target_platform="vercel",
                success=False,
                error_message=msg,
            )

        clean_slug = repo_slug.replace("https://github.com/", "").rstrip("/").replace(".git", "")
        # Clean service name for Vercel slug constraints (lowercase, alphanumeric, dashes)
        vercel_project_name = service_name.lower().replace("_", "-")

        # Map internal framework names to Vercel framework identifiers
        framework_map = {
            "react_vite": "vite",
            "vue_vite": "vite",
            "nextjs": "nextjs",
            "react_cra": "create-react-app",
            "static_html": None,
        }
        v_framework = framework_map.get(framework.lower(), "vite")

        # 1. Create or Find Project on Vercel
        project_id = None
        repo_id = None
        dashboard_url = f"https://vercel.com/{username}/{vercel_project_name}" if username else None

        try:
            emit(f"  -> Checking existing Vercel project '{vercel_project_name}'...")
            check_resp = requests.get(f"{self.base_url}/v9/projects/{vercel_project_name}", headers=self._headers(), timeout=15)
            if check_resp.status_code == 200:
                proj_data = check_resp.json()
                project_id = proj_data.get("id")
                repo_id = proj_data.get("link", {}).get("repoId")
                emit(f"  -> Found existing Vercel project '{vercel_project_name}' (ID: {project_id}).")
            else:
                emit(f"  -> Creating new Vercel project '{vercel_project_name}'...")
                proj_payload = {
                    "name": vercel_project_name,
                    "framework": v_framework,
                    "rootDirectory": root_dir if root_dir != "." else None,
                    "gitRepository": {
                        "type": "github",
                        "repo": clean_slug,
                    },
                }
                create_resp = requests.post(f"{self.base_url}/v9/projects", json=proj_payload, headers=self._headers(), timeout=20)
                if create_resp.status_code in (200, 201):
                    proj_data = create_resp.json()
                    project_id = proj_data.get("id")
                    repo_id = proj_data.get("link", {}).get("repoId")
                    emit(f"  [OK] Successfully created Vercel project '{vercel_project_name}' (ID: {project_id})!")
                else:
                    err_msg = f"Failed to create Vercel project: HTTP {create_resp.status_code} - {create_resp.text}"
                    emit(f"  [ERROR] {err_msg}")
                    return DeploymentResult(
                        service_name=service_name,
                        target_platform="vercel",
                        success=False,
                        error_message=err_msg,
                    )
        except Exception as e:
            err_msg = f"Exception interacting with Vercel Project API: {e}"
            emit(f"  [ERROR] {err_msg}")
            return DeploymentResult(
                service_name=service_name,
                target_platform="vercel",
                success=False,
                error_message=err_msg,
            )

        # 2. Resolve Numerical GitHub repoId if missing
        if not repo_id:
            repo_id = self.get_github_repo_id(clean_slug)

        # 3. Configure Environment Variables
        if env_vars and project_id:
            emit(f"  -> Configuring {len(env_vars)} environment variable(s) on Vercel project...")
            for k, v in env_vars.items():
                if not v:
                    continue
                env_payload = {
                    "key": k,
                    "value": str(v),
                    "type": "plain",
                    "target": ["production", "preview", "development"],
                }
                try:
                    requests.post(f"{self.base_url}/v10/projects/{project_id}/env", json=env_payload, headers=self._headers(), timeout=10)
                except Exception:
                    pass

        # 4. Create Deployment
        emit(f"  -> Dispatching deployment for branch '{branch}' on Vercel...")
        git_source = {
            "type": "github",
            "ref": branch,
        }
        if repo_id:
            git_source["repoId"] = str(repo_id)
        else:
            git_source["repo"] = clean_slug

        deploy_payload = {
            "name": vercel_project_name,
            "project": vercel_project_name,
            "gitSource": git_source,
        }

        deploy_id = None
        live_url = None
        try:
            dep_resp = requests.post(f"{self.base_url}/v13/deployments", json=deploy_payload, headers=self._headers(), timeout=25)
            if dep_resp.status_code in (200, 201):
                dep_data = dep_resp.json()
                deploy_id = dep_data.get("id")
                emit(f"  [OK] Vercel deployment triggered (ID: {deploy_id})!")
            else:
                err_msg = f"Vercel deploy trigger returned HTTP {dep_resp.status_code}: {dep_resp.text}"
                emit(f"  [ERROR] {err_msg}")
                return DeploymentResult(
                    service_name=service_name,
                    target_platform="vercel",
                    success=False,
                    dashboard_url=dashboard_url,
                    status="FAILED",
                    error_message=err_msg,
                    env_vars_configured=list(env_vars.keys()) if env_vars else [],
                )
        except Exception as e:
            err_msg = f"Exception triggering Vercel deploy: {e}"
            emit(f"  [ERROR] {err_msg}")
            return DeploymentResult(
                service_name=service_name,
                target_platform="vercel",
                success=False,
                dashboard_url=dashboard_url,
                status="FAILED",
                error_message=err_msg,
                env_vars_configured=list(env_vars.keys()) if env_vars else [],
            )

        # 5. Poll Deployment Status
        emit("  -> Waiting for Vercel build to complete and go live...")
        final_state = "BUILDING"
        if deploy_id:
            for _ in range(35):
                time.sleep(5)
                try:
                    s_resp = requests.get(f"{self.base_url}/v13/deployments/{deploy_id}", headers=self._headers(), timeout=10)
                    if s_resp.status_code == 200:
                        dep_info = s_resp.json()
                        state = dep_info.get("readyState", "BUILDING")
                        emit(f"     ... Vercel status: {state}")
                        if state == "READY":
                            final_state = "READY"
                            aliases = dep_info.get("alias", [])
                            # Always resolve the clean canonical production domain name (e.g. tmp29z606x1-client.vercel.app)
                            live_url = self.get_production_domain(vercel_project_name, aliases)
                            emit(f"  [OK] Resolved Vercel Production Domain: {live_url}")
                            break
                        elif state in ("ERROR", "CANCELED"):
                            final_state = state
                            emit(f"  [ERROR] Vercel build finished with state: {state}")
                            break
                except Exception:
                    pass

        is_success = (final_state == "READY") and (deploy_id is not None)
        if is_success and not live_url:
            live_url = self.get_production_domain(vercel_project_name)

        return DeploymentResult(
            service_name=service_name,
            target_platform="vercel",
            success=is_success,
            live_url=live_url if is_success else None,
            dashboard_url=dashboard_url,
            deployment_id=deploy_id,
            status=final_state,
            error_message=None if is_success else f"Vercel deployment failed with status '{final_state}'. Check {dashboard_url} for build logs.",
            env_vars_configured=list(env_vars.keys()) if env_vars else [],
        )
