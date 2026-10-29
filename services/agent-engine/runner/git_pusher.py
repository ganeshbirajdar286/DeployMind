import os
import subprocess
from pathlib import Path
from typing import Tuple, Optional, Dict
import requests


class GitPusher:
    """Automates Git branch creation, committing, and pushing to GitHub."""

    def __init__(self, token: Optional[str] = None):
        self.token = token or os.getenv("GITHUB_TOKEN")

    def push_branch(
        self,
        repo_dir: Path,
        branch_name: str = "ai-devops-setup",
        commit_message: str = "chore: add AI-generated DevOps and cloud sandbox configurations",
        github_repo: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Creates branch, stages files, commits, and pushes to remote."""
        repo_dir = Path(repo_dir).resolve()

        if not (repo_dir / ".git").exists():
            return False, f"Directory is not a git repository: {repo_dir}"

        try:
            # 1. Checkout new branch
            subprocess.run(["git", "checkout", "-B", branch_name], cwd=repo_dir, check=True, capture_output=True, text=True)

            # 2. Add all files
            subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True, text=True)

            # 3. Check if there are changes to commit
            status_res = subprocess.run(["git", "status", "--porcelain"], cwd=repo_dir, capture_output=True, text=True)
            if status_res.stdout.strip():
                # Commit changes
                subprocess.run(["git", "commit", "-m", commit_message], cwd=repo_dir, check=True, capture_output=True, text=True)

            # 4. Determine Push Remote / URL
            push_cmd = ["git", "push", "-u", "origin", branch_name]

            # If token and repo are provided, inject authenticated remote URL
            if self.token and github_repo:
                auth_url = f"https://x-access-token:{self.token}@github.com/{github_repo}.git"
                push_cmd = ["git", "push", "-u", auth_url, branch_name]

            push_res = subprocess.run(push_cmd, cwd=repo_dir, capture_output=True, text=True, timeout=60)
            if push_res.returncode == 0:
                return True, f"Successfully pushed branch '{branch_name}' to GitHub."
            else:
                return False, push_res.stderr or push_res.stdout

        except subprocess.CalledProcessError as e:
            err = e.stderr or str(e)
            return False, f"Git command failed: {err}"
        except Exception as ex:
            return False, f"Error pushing to GitHub: {str(ex)}"
