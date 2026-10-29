import base64
import getpass
import os
from typing import Tuple, Optional, List, Dict
import requests
from nacl import encoding, public


class GitHubSecretsManager:
    """Manages encryption and upload of repository secrets to GitHub Actions Secrets."""

    def __init__(self, token: Optional[str] = None):
        self.token = token or os.getenv("GITHUB_TOKEN")
        self.base_url = "https://api.github.com"

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "AI-DevOps-Agent-Secrets/1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def get_public_key(self, repo: str) -> Tuple[Optional[str], Optional[str]]:
        """Retrieves repository public key from GitHub (key_id, base64_public_key)."""
        url = f"{self.base_url}/repos/{repo}/actions/secrets/public-key"
        resp = requests.get(url, headers=self._headers(), timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("key_id"), data.get("key")
        return None, None

    @staticmethod
    def encrypt_secret(public_key_b64: str, secret_value: str) -> str:
        """Encrypts a secret string using libsodium SealedBox with the repository public key."""
        public_key = public.PublicKey(public_key_b64.encode("utf-8"), encoding.Base64Encoder())
        sealed_box = public.SealedBox(public_key)
        encrypted_bytes = sealed_box.encrypt(secret_value.encode("utf-8"))
        return base64.b64encode(encrypted_bytes).decode("utf-8")

    def upload_secret(self, repo: str, secret_name: str, secret_value: str) -> bool:
        """Encrypts and uploads a secret directly to GitHub Actions Secrets."""
        if not self.token:
            return False

        key_id, public_key_b64 = self.get_public_key(repo)
        if not key_id or not public_key_b64:
            return False

        encrypted_val = self.encrypt_secret(public_key_b64, secret_value)
        url = f"{self.base_url}/repos/{repo}/actions/secrets/{secret_name}"
        payload = {
            "encrypted_value": encrypted_val,
            "key_id": key_id,
        }

        resp = requests.put(url, json=payload, headers=self._headers(), timeout=10)
        return resp.status_code in (201, 204)

    def interactive_configure_secrets(
        self, repo: str, detected_vars: List[str]
    ) -> Dict[str, bool]:
        """Interactively asks the user in terminal to provide values for detected secrets."""
        if not detected_vars or not self.token:
            return {}

        results: Dict[str, bool] = {}

        print("\n" + "=" * 60)
        print("🔐 GitHub Actions Secrets Configuration")
        print("=" * 60)
        print(f"Target Repository: {repo}")
        print(f"Detected {len(detected_vars)} required environment variable(s):")
        for v in detected_vars:
            print(f"  - {v}")

        choice = input("\nWould you like to configure these secrets on GitHub Actions now? [Y/n]: ").strip().lower()
        if choice in ("n", "no"):
            print("Skipped secrets configuration. Sandbox will use default/mock values.\n")
            return results

        print("\n(Note: Inputs are masked for your privacy. Press Enter without typing to skip a variable)\n")

        for idx, var_name in enumerate(detected_vars, 1):
            try:
                # Masked input for security
                val = getpass.getpass(prompt=f"[{idx}/{len(detected_vars)}] Enter value for '{var_name}': ").strip()
            except Exception:
                val = input(f"[{idx}/{len(detected_vars)}] Enter value for '{var_name}': ").strip()

            if not val:
                print(f"  -> Skipped '{var_name}'.")
                continue

            print(f"  -> Encrypting and uploading '{var_name}' to GitHub Actions Secrets...")
            success = self.upload_secret(repo, var_name, val)
            results[var_name] = success

            if success:
                print(f"  [OK] Successfully configured '{var_name}' in repository secrets!")
            else:
                print(f"  [FAIL] Could not upload '{var_name}'. Verify token permissions (repo scope required).")

        print("=" * 60 + "\n")
        return results
