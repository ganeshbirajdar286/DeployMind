import base64
from nacl import public
from ai_devops_agent.runner.secrets_manager import GitHubSecretsManager
from ai_devops_agent.utils.diff_viewer import format_colored_diff


def test_format_colored_diff():
    old_text = "FROM node:18\nWORKDIR /app\n"
    new_text = "FROM node:20-alpine\nWORKDIR /app\nEXPOSE 3000\n"

    diff_str = format_colored_diff("Dockerfile", old_text, new_text)
    assert "Dockerfile" in diff_str
    assert "-FROM node:18" in diff_str
    assert "+FROM node:20-alpine" in diff_str
    assert "+EXPOSE 3000" in diff_str


def test_github_secrets_encryption():
    # Generate test public key
    keypair = public.PrivateKey.generate()
    public_key_b64 = base64.b64encode(bytes(keypair.public_key)).decode("utf-8")

    secret_val = "sk-test-super-secret-key-12345"
    encrypted_val = GitHubSecretsManager.encrypt_secret(public_key_b64, secret_val)

    assert isinstance(encrypted_val, str)
    assert len(encrypted_val) > 20
    # Decrypt with private key to verify correctness
    sealed_box = public.SealedBox(keypair)
    decrypted_bytes = sealed_box.decrypt(base64.b64decode(encrypted_val))
    assert decrypted_bytes.decode("utf-8") == secret_val


def test_interactive_edit_bundle_quit(tmp_path, monkeypatch):
    from ai_devops_agent.agent.autonomous_loop import interactive_edit_bundle

    artifacts = {
        "Dockerfile": "FROM node:20-alpine",
        "docker-compose.yml": "services:\n  app:\n    build: .",
    }
    # User types 'b' immediately
    monkeypatch.setattr("builtins.input", lambda prompt="": "b")
    result = interactive_edit_bundle(tmp_path, artifacts)
    assert result == artifacts


def test_interactive_configure_secrets_paste_and_custom(tmp_path, monkeypatch):
    mgr = GitHubSecretsManager(token=None)
    
    # Simulate user choosing Option 3 (Paste block), pasting two lines, empty line to finish,
    # then responding 'y' to add custom variable, adding 'BREVO_API_KEY', value, then empty line.
    inputs = iter([
        "3",  # Choose Option 3
        "FRONTEND_URL=https://myportfolio.vercel.app",
        "MY_PERSONAL_EMAIL=test@example.com",
        "",   # finish paste
        "y",  # add additional variable
        "BREVO_API_KEY",
        "test-brevo-api-key-12345",
        "",   # finish custom
    ])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    res = mgr.interactive_configure_secrets(
        repo="test/repo",
        detected_vars=["FRONTEND_URL"],
        local_env_dir=tmp_path,
    )

    assert "FRONTEND_URL" in res
    assert res["FRONTEND_URL"] == "https://myportfolio.vercel.app"
    assert "MY_PERSONAL_EMAIL" in res
    assert res["MY_PERSONAL_EMAIL"] == "test@example.com"
    assert "BREVO_API_KEY" in res
    assert res["BREVO_API_KEY"] == "test-brevo-api-key-12345"

    # Verify local .env file was synchronized
    env_file = tmp_path / ".env"
    assert env_file.exists()
    content = env_file.read_text(encoding="utf-8")
    assert "BREVO_API_KEY=test-brevo-api-key-12345" in content
    assert "FRONTEND_URL=https://myportfolio.vercel.app" in content


def test_interactive_configure_secrets_load_file(tmp_path, monkeypatch):
    mgr = GitHubSecretsManager(token=None)

    # Create dummy .env file
    dummy_env = tmp_path / "custom.env"
    dummy_env.write_text("MOGODB_URL=mongodb://localhost:27017/test\nJWT_SECRET=supersecret123\n", encoding="utf-8")

    inputs = iter([
        "2",  # Choose Option 2 (Load from file)
        str(dummy_env),
        "n",  # Do not add additional variables
    ])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    res = mgr.interactive_configure_secrets(
        repo="test/repo",
        detected_vars=["MOGODB_URL", "JWT_SECRET"],
        local_env_dir=tmp_path,
    )

    assert res["MOGODB_URL"] == "mongodb://localhost:27017/test"
    assert res["JWT_SECRET"] == "supersecret123"


def test_interactive_configure_secrets_skip(tmp_path, monkeypatch):
    mgr = GitHubSecretsManager(token=None)

    inputs = iter([
        "4",  # Choose Option 4 (Skip)
    ])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    res = mgr.interactive_configure_secrets(
        repo="test/repo",
        detected_vars=["PORT"],
        local_env_dir=tmp_path,
    )

    assert res == {}


