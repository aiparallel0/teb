"""TOTP-based two-factor authentication."""
import hmac, hashlib, struct, time, secrets, base64
from typing import Optional, Tuple


def generate_secret() -> str:
    """Generate a base32-encoded TOTP secret."""
    return base64.b32encode(secrets.token_bytes(20)).decode('ascii')


def get_totp_uri(secret: str, email: str, issuer: str = "teb") -> str:
    """Generate otpauth:// URI for QR code scanning."""
    from urllib.parse import quote
    return f"otpauth://totp/{quote(issuer)}:{quote(email)}?secret={secret}&issuer={quote(issuer)}"


def generate_totp(secret: str, time_step: int = 30, digits: int = 6) -> str:
    """Generate current TOTP code."""
    key = base64.b32decode(secret, casefold=True)
    counter = int(time.time()) // time_step
    msg = struct.pack('>Q', counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code = struct.unpack('>I', h[offset:offset+4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


def verify_totp(secret: str, code: str, time_step: int = 30, window: int = 1) -> bool:
    """Verify TOTP code with time window tolerance."""
    key = base64.b32decode(secret, casefold=True)
    current_time = int(time.time())
    for offset in range(-window, window + 1):
        counter = (current_time // time_step) + offset
        msg = struct.pack('>Q', counter)
        h = hmac.new(key, msg, hashlib.sha1).digest()
        o = h[-1] & 0x0F
        token = str((struct.unpack('>I', h[o:o+4])[0] & 0x7FFFFFFF) % (10 ** 6)).zfill(6)
        if hmac.compare_digest(token, code):
            return True
    return False


def generate_backup_codes(count: int = 8) -> list:
    """Generate one-time backup codes."""
    return [secrets.token_hex(4) for _ in range(count)]
