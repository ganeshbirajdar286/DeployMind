import os
import time
from typing import Dict, List, Optional, Tuple, Callable
import requests
from .models import DeploymentResult


class RenderDeployer:
    """Manages deployments of backend web services to Render via REST API."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("RENDER_API_KEY")
        self.base_url = "https://api.render.com/v1"

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def verify_credentials(self) -> Tuple[bool, Optional[str], str]:
        """Verifies Render API token and retrieves primary owner ID."""
        if not self.api_key:
            return False, None, "No RENDER_API_KEY provided."
        try:
            resp = requests.get(f"{self.base_url}/owners", headers=self._headers(), timeout=15)
            if resp.status_code == 200:
                owners = resp.json()
                if owners and isinstance(owners, list):
                    owner_obj = owners[0].get("owner", {})
                    owner_id = owner_obj.get("id")
                    owner_name = owner_obj.get("name") or owner_obj.get("email")
                    return True, owner_id, f"Authenticated as Render owner '{owner_name}'."
            return False, None, f"Render authentication failed (HTTP {resp.status_code}): {resp.text}"
        except Exception as e:
            return False, None, f"Error connecting to Render API: {e}"

    def sync_all_env_vars(self, service_id: str, env_vars: Dict[str, str]) -> bool:
        """Overwrites/replaces all environment variables for a Render service via PUT /services/{id}/env-vars."""
        if not self.api_key or not service_id:
            return False
        payload = [{"key": k, "value": str(v)} for k, v in env_vars.items()]
        try:
            resp = requests.put(
                f"{self.base_url}/services/{service_id}/env-vars",
                json=payload,
                headers=self._headers(),
                timeout=15,
            )
            return resp.status_code in (200, 201)
        except Exception:
            return False

    def set_single_env_var(self, service_id: str, key: str, value: str) -> bool:
        """Adds or updates a single environment variable on Render via PUT /services/{id}/env-vars/{key}."""
        if not self.api_key or not service_id:
            return False
        payload = {"key": key, "value": str(value)}
        try:
            resp = requests.put(
                f"{self.base_url}/services/{service_id}/env-vars/{key}",
                json=payload,
                headers=self._headers(),
                timeout=15,
            )
            return resp.status_code in (200, 201)
        except Exception:
            return False

    def trigger_deploy(self, service_id: str, clear_cache: bool = False) -> Optional[str]:
        """Triggers a manual deploy on Render and returns deploy ID."""
        if not self.api_key or not service_id:
            return None
        payload = {"clearCache": "clear" if clear_cache else "do_not_clear"}
        try:
            resp = requests.post(
                f"{self.base_url}/services/{service_id}/deploys",
                json=payload,
                headers=self._headers(),
                timeout=15,
            )
            if resp.status_code in (200, 201):
                return resp.json().get("id")
        except Exception:
            pass
        return None

    def get_latest_deploy(self, service_id: str) -> Tuple[Optional[str], str]:
        """Retrieves the latest deploy ID and its status for a Render service."""
        if not self.api_key or not service_id:
            return None, "unknown"
        try:
            resp = requests.get(f"{self.base_url}/services/{service_id}/deploys?limit=1", headers=self._headers(), timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data and isinstance(data, list) and len(data) > 0:
                    dep = data[0].get("deploy", {})
                    return dep.get("id"), dep.get("status", "unknown")
        except Exception:
            pass
        return None, "unknown"

    def deploy_web_service(
        self,
        repo_url: str,
        branch: str,
        service_name: str,
        root_dir: str = "backend",
        env_vars: Optional[Dict[str, str]] = None,
        port: int = 3000,
        is_docker: bool = True,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> DeploymentResult:
        """Creates or triggers a Web Service on Render, updates environment variables, and waits for completion."""
        def emit(msg: str):
            if log_callback:
                log_callback(msg)

        emit(f"[RenderDeployer] Preparing deployment for '{service_name}' on Render...")
        ok, owner_id, msg = self.verify_credentials()
        if not ok or not owner_id:
            emit(f"  [ERROR] {msg}")
            return DeploymentResult(
                service_name=service_name,
                target_platform="render",
                success=False,
                error_message=msg,
            )

        env_vars = dict(env_vars or {})
        if "PORT" not in env_vars:
            env_vars["PORT"] = str(port)

        env_var_list = [{"key": k, "value": str(v)} for k, v in env_vars.items()]

        # 1. Check if service already exists
        service_id = None
        live_url = None
        dashboard_url = None

        try:
            emit(f"  -> Checking existing Render services matching '{service_name}'...")
            list_resp = requests.get(f"{self.base_url}/services?name={service_name}", headers=self._headers(), timeout=15)
            if list_resp.status_code == 200:
                services = list_resp.json()
                for s in services:
                    svc = s.get("service", {})
                    if svc.get("name") == service_name:
                        service_id = svc.get("id")
                        service_details = svc.get("serviceDetails", {})
                        live_url = service_details.get("url")
                        dashboard_url = f"https://dashboard.render.com/web/{service_id}"
                        emit(f"  -> Found existing Render service '{service_name}' (ID: {service_id}).")
                        break
        except Exception as e:
            emit(f"  -> Warning checking services: {e}")

        # 2. Create Service if not found
        clean_repo = repo_url
        if clean_repo.endswith(".git"):
            clean_repo = clean_repo[:-4]

        if not service_id:
            emit(f"  -> Creating new Render Web Service '{service_name}'...")
            create_payload = {
                "type": "web_service",
                "name": service_name,
                "ownerId": owner_id,
                "repo": clean_repo,
                "branch": branch,
                "autoDeploy": "yes",
                "rootDir": root_dir if root_dir != "." else "",
                "serviceDetails": {
                    "env": "docker" if is_docker else "node",
                    "plan": "free",
                    "region": "oregon",
                },
                "envVars": env_var_list,
            }

            try:
                resp = requests.post(f"{self.base_url}/services", json=create_payload, headers=self._headers(), timeout=20)
                if resp.status_code in (201, 200):
                    data = resp.json()
                    # Render REST API v1 returns {"service": {"id": "srv-...", "serviceDetails": {"url": "..."}}}
                    svc_data = data.get("service", {}) if isinstance(data.get("service"), dict) else data
                    service_id = svc_data.get("id") or data.get("id")
                    details = svc_data.get("serviceDetails", {}) or data.get("serviceDetails", {})
                    live_url = details.get("url") or svc_data.get("url")
                    dashboard_url = f"https://dashboard.render.com/web/{service_id}" if service_id else None
                    emit(f"  [OK] Successfully created Render service '{service_name}' (ID: {service_id})!")
                else:
                    err_msg = f"Failed to create Render service: HTTP {resp.status_code} - {resp.text}"
                    emit(f"  [ERROR] {err_msg}")
                    return DeploymentResult(
                        service_name=service_name,
                        target_platform="render",
                        success=False,
                        error_message=err_msg,
                    )
            except Exception as e:
                err_msg = f"Exception creating Render service: {e}"
                emit(f"  [ERROR] {err_msg}")
                return DeploymentResult(
                    service_name=service_name,
                    target_platform="render",
                    success=False,
                    error_message=err_msg,
                )

        # 3. Synchronize Environment Variables (guarantees Render has all variables regardless of creation state)
        if service_id and env_vars:
            emit(f"  -> Synchronizing {len(env_vars)} environment variable(s) to Render service...")
            sync_ok = self.sync_all_env_vars(service_id, env_vars)
            if sync_ok:
                emit(f"  [OK] Synchronized environment variables to Render ({', '.join(env_vars.keys())})!")
            else:
                emit("  [!] Warning: Render env-vars endpoint did not return 200. Proceeding with deploy...")

        # 4. Trigger Deploy
        emit(f"  -> Triggering new deployment on Render for branch '{branch}'...")
        deploy_id = self.trigger_deploy(service_id, clear_cache=False)
        if not deploy_id and service_id:
            # Fallback: get current/active deploy ID started automatically by Render
            deploy_id, current_st = self.get_latest_deploy(service_id)

        if deploy_id:
            emit(f"  [OK] Deployment dispatched successfully (Deploy ID: {deploy_id})!")
        else:
            emit("  -> Deploy trigger dispatched. Monitoring active deploy...")

        # 5. Poll Deployment Status
        emit("  -> Waiting for Render service to build and go live...")
        final_status = "unknown"
        if service_id:
            for attempt in range(40):
                time.sleep(6)
                if not deploy_id:
                    deploy_id, _ = self.get_latest_deploy(service_id)

                if deploy_id:
                    try:
                        status_resp = requests.get(f"{self.base_url}/services/{service_id}/deploys/{deploy_id}", headers=self._headers(), timeout=10)
                        if status_resp.status_code == 200:
                            st = status_resp.json().get("status", "in_progress")
                            emit(f"     ... Render status: {st}")
                            if st == "live":
                                final_status = "live"
                                break
                            elif st in ("build_failed", "update_failed", "canceled", "deactivated"):
                                final_status = st
                                emit(f"  [ERROR] Render build finished with status: {st}")
                                break
                    except Exception:
                        pass
                else:
                    emit(f"     ... Waiting for Render build (Attempt {attempt+1}/40)...")

        is_success = (final_status == "live")
        if is_success and live_url:
            try:
                probe = requests.get(live_url, timeout=10)
                emit(f"  [OK] Render live health probe to {live_url} returned HTTP {probe.status_code}")
            except Exception:
                pass

        return DeploymentResult(
            service_name=service_name,
            target_platform="render",
            success=is_success,
            live_url=live_url if is_success else None,
            dashboard_url=dashboard_url,
            service_id=service_id,
            deployment_id=deploy_id,
            status=final_status,
            error_message=None if is_success else f"Render deployment concluded with status '{final_status}'. Check {dashboard_url} for build logs.",
            env_vars_configured=list(env_vars.keys()),
        )
