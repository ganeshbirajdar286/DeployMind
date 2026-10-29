import base64
from nacl import public
from runner.secrets_manager import GitHubSecretsManager
from utils.diff_viewer import format_colored_diff


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
