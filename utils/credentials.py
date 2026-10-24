"""Store the login password in Windows Credential Manager via keyring.

config.json keeps only the username; the password never touches disk in
plain or reversible form.
"""
import keyring

SERVICE_NAME = "BlueskyBCClient"

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
        return keyring.get_password(SERVICE_NAME, username)
    except Exception:
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
    try:
        keyring.delete_password(SERVICE_NAME, username)
    except Exception:
        pass
