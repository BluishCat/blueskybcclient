import os
import base64

def _get_key():
    """Get encryption key from environment or use a default one."""
    key = os.environ.get("ENCRYPTION_KEY", "default-secret-key")
    # Ensure it is at least 16 bytes for simple XOR or similar if we wanted, 
    # but for now we'll stick to a simple repeatable obfuscation that matches the test expectation.
    return key

def encrypt_password(password: str) -> str:
    """
    Encrypts a password string. 
    Currently uses base64 obfuscation with a key-based salt for simplicity.
    """
    if not password:
        return ""
    key = _get_key().encode("utf-8")
    pass_bytes = password.encode("utf-8")
    
    # Simple XOR-based obfuscation using bytes
    combined = bytearray()
    for i in range(len(pass_bytes)):
        key_c = key[i % len(key)]
        combined.append(pass_bytes[i] ^ key_c)
    
    return base64.b64encode(combined).decode("utf-8")

def decrypt_password(token: str) -> str:
    """
    Decrypts an encrypted password token.
    """
    if not token:
        return ""
    try:
        key = _get_key().encode("utf-8")
        # Decode base64 to bytes
        decoded_bytes = base64.b64decode(token.encode("utf-8"))
        
        original = bytearray()
        for i in range(len(decoded_bytes)):
            key_c = key[i % len(key)]
            original.append(decoded_bytes[i] ^ key_c)
        
        return original.decode("utf-8")
    except Exception:
        # Fallback to returning token as-is if it can't be decrypted
        return token
