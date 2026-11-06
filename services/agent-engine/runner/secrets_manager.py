import base64
import getpass
import os
from pathlib import Path
from typing import Tuple, Optional, List, Dict
import requests
from nacl import encoding, public
from utils.theme import (
    print_step_box,
    print_box,
    prompt_choice,
    CYAN,
    PINK,
    PURPLE,
    EMERALD,
    AMBER,
    WHITE,
    MUTED,
    BOLD,
    DIM,
    RESET,
)


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
        self, repo: str, detected_vars: List[str], local_env_dir: Optional[Path] = None
    ) -> Dict[str, str]:
        """Interactively asks the user in terminal to provide values for detected and custom secrets."""
        results: Dict[str, str] = {}
        detected_vars = list(detected_vars or [])

        # 1. Discover any existing values from local .env files
        existing_local_values: Dict[str, str] = {}
        if local_env_dir:
            search_paths = [
                Path(local_env_dir) / ".env",
                Path(local_env_dir) / ".env.local",
                Path(local_env_dir) / ".env.production",
            ]
            # Also search immediate subdirectories (e.g. backend/.env)
            try:
                for child in Path(local_env_dir).iterdir():
                    if child.is_dir() and not child.name.startswith("."):
                        search_paths.append(child / ".env")
            except Exception:
                pass

            for p in search_paths:
                if p.exists() and p.is_file():
                    try:
                        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                            line = line.strip()
                            if line and not line.startswith("#") and "=" in line:
                                k, v = line.split("=", 1)
                                k = k.strip()
                                v = v.strip().strip("'\"")
                                if v and k not in existing_local_values:
                                    existing_local_values[k] = v
                    except Exception:
                        pass

        step2_lines = [
            f"{MUTED}Target Repository:{RESET} {BOLD}{WHITE}{repo}{RESET}",
        ]
        if detected_vars:
            step2_lines.append(f"{CYAN}Detected {len(detected_vars)} variable reference(s) in codebase:{RESET}")
            for v in detected_vars:
                source = f" {DIM}(found in local .env){RESET}" if v in existing_local_values else ""
                step2_lines.append(f"  {MUTED}•{RESET} {BOLD}{v}{RESET}{source}")
        else:
            step2_lines.append(f"{MUTED}No environment variables were automatically detected in the codebase.{RESET}")

        step2_lines.extend([
            "",
            f"{BOLD}{WHITE}Select configuration method:{RESET}",
            f"  {PINK}[1]{RESET} ⚡ Interactive Entry       {DIM}(Enter values one-by-one — Recommended){RESET}",
            f"  {CYAN}[2]{RESET} 📂 Load from .env File      {DIM}(Provide path to .env file){RESET}",
            f"  {PURPLE}[3]{RESET} 📋 Paste Environment Block {DIM}(Paste multi-line KEY=VALUE lines){RESET}",
            f"  {MUTED}[4]{RESET} ⏭️  Skip / Mock Defaults     {DIM}(Auto-fill sandbox mock defaults){RESET}",
        ])

        print_step_box(
            step_num=2,
            total_steps=5,
            title="Environment Variables & Secrets Configuration",
            lines=step2_lines,
            badge="🔐",
            border_color=AMBER,
        )

        choice = prompt_choice("Choose an option [1-4]", default="1")

        collected_values: Dict[str, str] = {}

        # ----------------------------------------------------
        # OPTION 2: Load from .env file path
        # ----------------------------------------------------
        if choice == "2":
            try:
                env_path_str = input("\nEnter path to .env file (or press Enter to cancel): ").strip()
            except Exception:
                env_path_str = ""

            if env_path_str:
                env_p = Path(env_path_str).expanduser().resolve()
                if env_p.exists() and env_p.is_file():
                    for line in env_p.read_text(encoding="utf-8", errors="ignore").splitlines():
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip("'\"")
                            if k:
                                collected_values[k] = v
                    print(f"  [OK] Loaded {len(collected_values)} variable(s) from '{env_p.name}'.")
                else:
                    print(f"  [!] File '{env_path_str}' not found. Falling back to interactive entry.")
                    choice = "1"
            else:
                choice = "1"

        # ----------------------------------------------------
        # OPTION 3: Paste environment block
        # ----------------------------------------------------
        if choice == "3":
            print("\nPaste your environment variables below (KEY=VALUE).")
            print("Press Enter on an empty line or press Ctrl+Z/Ctrl+D when finished:\n")
            pasted_lines = []
            while True:
                try:
                    line = input()
                    if not line.strip():
                        if pasted_lines:
                            break
                        continue
                    pasted_lines.append(line)
                except EOFError:
                    break
                except Exception:
                    break

            for line in pasted_lines:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k:
                        collected_values[k] = v

            print(f"\n  [OK] Parsed {len(collected_values)} variable(s) from pasted block.")

        # ----------------------------------------------------
        # OPTION 1: Enter values interactively
        # ----------------------------------------------------
        if choice == "1":
            print("\nEnter values for detected variables (press Enter to use mock sandbox default):")
            for idx, var_name in enumerate(detected_vars, 1):
                existing_val = existing_local_values.get(var_name, "")
                prompt_label = f"[{idx}/{len(detected_vars)}] {var_name}"
                if existing_val:
                    prompt_label += f" [press Enter to keep existing]"

                try:
                    val = input(f"{prompt_label}: ").strip()
                except Exception:
                    val = ""

                if not val and existing_val:
                    val = existing_val

                if val:
                    collected_values[var_name] = val
                    print(f"    -> Set '{var_name}'")
                else:
                    print(f"    -> Skipped '{var_name}' (will use mock default)")

        # ----------------------------------------------------
        # Ask if user wants to add any additional variables (e.g. BREVO_API_KEY)
        # ----------------------------------------------------
        if choice in ("1", "2", "3"):
            try:
                add_more = input("\nWould you like to add any additional environment variables (e.g. BREVO_API_KEY)? [y/N]: ").strip().lower()
            except Exception:
                add_more = "n"

            if add_more in ("y", "yes"):
                print("Enter custom variables as NAME and VALUE (leave NAME empty to finish):")
                while True:
                    try:
                        k = input("  Variable Name: ").strip()
                    except Exception:
                        break
                    if not k:
                        break
                    try:
                        v = input(f"  Value for '{k}': ").strip()
                    except Exception:
                        v = ""
                    if k and v:
                        collected_values[k] = v
                        print(f"    [OK] Added '{k}'")

        if not collected_values and choice == "4":
            print("  Skipped secrets entry. Cloud Sandbox will use safe mock values.\n")
            print("=" * 60 + "\n")
            return results

        # ----------------------------------------------------
        # 4. Upload to GitHub Actions Secrets (if token available)
        # ----------------------------------------------------
        if self.token and collected_values:
            print(f"\n🔐 Encrypting and uploading {len(collected_values)} secret(s) to GitHub Actions Secrets...")
            for k, v in collected_values.items():
                if v:
                    ok = self.upload_secret(repo, k, v)
                    if ok:
                        print(f"  [OK] Successfully configured '{k}' in repository secrets!")
                        results[k] = v
                    else:
                        print(f"  [!] Failed to upload '{k}' to GitHub Secrets (verify repo token permissions).")
                        results[k] = v
                else:
                    results[k] = v
        else:
            for k, v in collected_values.items():
                results[k] = v

        # ----------------------------------------------------
        # 5. Synchronize into local .env files (root and subdirectories)
        # ----------------------------------------------------
        if local_env_dir and collected_values:
            target_env_files = [Path(local_env_dir) / ".env"]
            try:
                for child in Path(local_env_dir).iterdir():
                    if child.is_dir() and not child.name.startswith("."):
                        target_env_files.append(child / ".env")
            except Exception:
                pass

            for env_file in target_env_files:
                try:
                    env_lines = []
                    if env_file.exists():
                        env_lines = env_file.read_text(encoding="utf-8").splitlines()
                    keys_present = set()
                    new_lines = []
                    for line in env_lines:
                        if line.strip() and not line.strip().startswith("#") and "=" in line:
                            k = line.split("=", 1)[0].strip()
                            if k in collected_values:
                                new_lines.append(f"{k}={collected_values[k]}")
                                keys_present.add(k)
                            else:
                                new_lines.append(line)
                        else:
                            new_lines.append(line)
                    for k, v in collected_values.items():
                        if k not in keys_present and v:
                            new_lines.append(f"{k}={v}")
                    env_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                except Exception:
                    pass

            print(f"  [OK] Synchronized {len(collected_values)} variable(s) into local workspace '.env'.")

        print("=" * 60 + "\n")
        return results
