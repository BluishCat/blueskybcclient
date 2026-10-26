"""Store the login password in Windows Credential Manager via keyring.

config.json keeps only the username; the password never touches disk in
plain or reversible form.
"""
import keyring

SERVICE_NAME = "Poisora"
# 改名前の資格情報名。見つかったら SERVICE_NAME へ移す
LEGACY_SERVICE_NAMES = ("BlueskyBCClient",)

_backend_ready = False


def _ensure_backend():
    """Pin the Windows backend so PyInstaller builds don't depend on entry-point discovery."""
    global _backend_ready
    if _backend_ready:
        return
    try:
        from keyring.backends.Windows import WinVaultKeyring
        keyring.set_keyring(WinVaultKeyring())
    except Exception:
        pass  # Non-Windows / dev environment: fall back to keyring's default
    _backend_ready = True


def get_password(username: str):
    if not username:
        return None
    _ensure_backend()
    try:
        password = keyring.get_password(SERVICE_NAME, username)
    except Exception:
        return None
    if password:
        return password
    return _migrate_legacy(username)


def _migrate_legacy(username: str):
    """旧名で保存されたパスワードを新しい名前へ移し、そのパスワードを返す"""
    for legacy in LEGACY_SERVICE_NAMES:
        try:
            password = keyring.get_password(legacy, username)
        except Exception:
            continue
        if not password:
            continue
        try:
            keyring.set_password(SERVICE_NAME, username, password)
            keyring.delete_password(legacy, username)
        except Exception:
            pass  # 移せなくても今回のログインには使える
        return password
    return None


def set_password(username: str, password: str) -> bool:
    if not username or not password:
        return False
    _ensure_backend()
    try:
        keyring.set_password(SERVICE_NAME, username, password)
        return True
    except Exception:
        return False


def delete_password(username: str) -> None:
    if not username:
        return
    _ensure_backend()
    for service in (SERVICE_NAME, *LEGACY_SERVICE_NAMES):
        try:
            keyring.delete_password(service, username)
        except Exception:
            pass
