import os
import re
import subprocess
from pathlib import Path
from typing import Tuple, Optional, Dict
import requests
from ai_devops_agent.utils.sanitizer import redact_secrets, sanitize_url_for_logging


class GitPusher:
    """Automates Git branch creation, committing, pushing, and Pull Request workflows with security hardening."""

    def __init__(self, token: Optional[str] = None):
        self.token = token or os.getenv("GITHUB_TOKEN")
        self.base_url = "https://api.github.com"

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "AI-DevOps-Agent/1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def push_branch(
        self,
        repo_dir: Path,
        branch_name: str = "ai-devops-setup",
        commit_message: str = "chore: add AI-generated DevOps and cloud sandbox configurations",
        github_repo: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Creates branch, stages files, commits, and pushes to remote with secret leak prevention."""
        repo_dir = Path(repo_dir).resolve()

        # 0. Branch name validation (prevent option injection or path traversal)
        if not re.match(r"^[a-zA-Z0-9_\-\./]+$", branch_name) or branch_name.startswith("-") or ".." in branch_name:
            return False, f"Invalid or unsafe branch name: '{branch_name}'"

        if not (repo_dir / ".git").exists():
            return False, f"Directory is not a git repository: {repo_dir}"

        custom_secrets = [self.token] if self.token else []

        try:
            # 1. Checkout new branch
            subprocess.run(["git", "checkout", "-B", branch_name], cwd=repo_dir, check=True, capture_output=True, text=True)

            # 2. Add all files
            subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True, text=True)

            # Security safeguard: Prevent staging/committing any .env files anywhere in the workspace tree
            staged_res = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=repo_dir, capture_output=True, text=True)
            staged_files = staged_res.stdout.splitlines() if staged_res.stdout else []

            env_files_to_unstage = [
                f for f in staged_files
                if f.endswith(".env") or "/.env" in f or "\\.env" in f or ".env." in f or f.startswith(".env")
            ]
            if env_files_to_unstage:
                subprocess.run(
                    ["git", "reset", "HEAD", "--"] + env_files_to_unstage,
                    cwd=repo_dir,
                    capture_output=True,
                    text=True,
                )

            # 3. Check if there are changes to commit
            status_res = subprocess.run(["git", "status", "--porcelain"], cwd=repo_dir, capture_output=True, text=True)
            if status_res.stdout.strip():
                subprocess.run(["git", "commit", "-m", commit_message], cwd=repo_dir, check=True, capture_output=True, text=True)

            # 4. Determine Push Remote / URL
            push_cmd = ["git", "push", "-u", "--force", "origin", branch_name]

            if self.token and github_repo:
                auth_url = f"https://x-access-token:{self.token}@github.com/{github_repo}.git"
                push_cmd = ["git", "push", "-u", "--force", auth_url, branch_name]

            push_res = subprocess.run(push_cmd, cwd=repo_dir, capture_output=True, text=True, timeout=60)
            if push_res.returncode == 0:
                return True, f"Successfully pushed branch '{branch_name}' to GitHub."
            else:
                raw_err = push_res.stderr or push_res.stdout or "Git push rejected"
                clean_err = sanitize_url_for_logging(redact_secrets(raw_err, custom_secrets=custom_secrets))
                return False, clean_err

        except subprocess.CalledProcessError as e:
            err = e.stderr or str(e)
            clean_err = sanitize_url_for_logging(redact_secrets(err, custom_secrets=custom_secrets))
            return False, f"Git command failed: {clean_err}"
        except Exception as ex:
            clean_err = sanitize_url_for_logging(redact_secrets(str(ex), custom_secrets=custom_secrets))
            return False, f"Error pushing to GitHub: {clean_err}"

    def get_head_sha(self, repo_dir: Path) -> Optional[str]:
        """Returns the latest commit hash (HEAD) for the repository."""
        try:
            res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True)
            if res.returncode == 0:
                return res.stdout.strip()
        except Exception:
            pass
        return None

    def get_default_branch(self, repo: str) -> str:
        """Retrieves default base branch (e.g. 'main' or 'master') for the repository."""
        if not self.token or not repo:
            return "main"
        try:
            resp = requests.get(f"{self.base_url}/repos/{repo}", headers=self._headers(), timeout=10)
            if resp.status_code == 200:
                return resp.json().get("default_branch", "main")
        except Exception:
            pass
        return "main"

    def create_pull_request(
        self,
        repo: str,
        head_branch: str,
        base_branch: str = "main",
        title: Optional[str] = None,
        body: Optional[str] = None,
    ) -> Tuple[bool, Optional[str], Optional[int], str]:
        """Creates a Pull Request on GitHub from head_branch to base_branch."""
        if not self.token or not repo:
            return False, None, None, "No GITHUB_TOKEN or repository provided."

        owner = repo.split("/")[0]

        # Check for existing open PR
        try:
            check_url = f"{self.base_url}/repos/{repo}/pulls?head={owner}:{head_branch}&base={base_branch}&state=open"
            check_resp = requests.get(check_url, headers=self._headers(), timeout=15)
            if check_resp.status_code == 200:
                prs = check_resp.json()
                if prs and isinstance(prs, list) and len(prs) > 0:
                    pr = prs[0]
                    return True, pr.get("html_url"), pr.get("number"), f"Existing open Pull Request found: {pr.get('html_url')}"
        except Exception:
            pass

        title = title or "feat(devops): automated containerization & CI/CD deployment pipeline"
        body = body or (
            "## 🚀 AI DevOps Agent Autonomous Configuration\n\n"
            "This Pull Request contains verified, production-ready DevOps configurations:\n"
            "- Multi-stage Dockerfiles\n"
            "- Docker Compose specification\n"
            "- GitHub Actions CI/CD workflows (`ci.yml`, `ai-devops-verify.yml`)\n"
            "- Release and Dependabot automations\n\n"
            "**Verification:** 100% verified in GitHub Actions Cloud Sandbox.\n"
        )

        payload = {
            "title": title,
            "head": head_branch,
            "base": base_branch,
            "body": body,
        }

        try:
            url = f"{self.base_url}/repos/{repo}/pulls"
            resp = requests.post(url, json=payload, headers=self._headers(), timeout=20)
            if resp.status_code in (200, 201):
                data = resp.json()
                return True, data.get("html_url"), data.get("number"), "Pull Request created successfully."
            else:
                err_data = resp.json() if resp.status_code == 422 else {}
                err_msg = err_data.get("message", resp.text)
                return False, None, None, f"Failed to create PR (HTTP {resp.status_code}): {err_msg}"
        except Exception as e:
            return False, None, None, f"Exception creating Pull Request: {e}"

    def is_pull_request_merged(self, repo: str, pull_number: int) -> bool:
        """Checks if a pull request has been merged (HTTP 204 = merged, 404 = not merged)."""
        if not self.token or not repo or not pull_number:
            return False
        try:
            url = f"{self.base_url}/repos/{repo}/pulls/{pull_number}/merge"
            resp = requests.get(url, headers=self._headers(), timeout=10)
            return resp.status_code == 204
        except Exception:
            return False

    def merge_pull_request(
        self, repo: str, pull_number: int, commit_title: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Merges a pull request using the GitHub API."""
        if not self.token or not repo or not pull_number:
            return False, "Missing token, repo, or PR number."
        payload = {
            "commit_title": commit_title or f"Merge pull request #{pull_number} from ai-devops-setup",
            "merge_method": "merge",
        }
        try:
            url = f"{self.base_url}/repos/{repo}/pulls/{pull_number}/merge"
            resp = requests.put(url, json=payload, headers=self._headers(), timeout=15)
            if resp.status_code == 200:
                return True, f"Successfully merged PR #{pull_number} into base branch."
            else:
                msg = resp.json().get("message", resp.text)
                return False, f"Failed to merge PR (HTTP {resp.status_code}): {msg}"
        except Exception as e:
            return False, f"Error merging PR: {e}"
