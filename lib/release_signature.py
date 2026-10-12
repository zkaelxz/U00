"""
lib/release_signature.py -- the Ed25519 release signature format, shared by
the in-app updater (services/update_service.py) and
tools/release_signing.py. The release workflow
(.github/workflows/windows-installer.yml) builds the same message with
printf and signs it with openssl, so a change here changes it there too.

What is signed is a short statement naming the installer file and the
SHA-256 of its bytes, not the bytes themselves:
- the name carries the version, so an older, correctly signed installer
  re-published under a newer tag doesn't verify (no signed downgrade);
- the hash is taken while streaming, whereas Ed25519 (one-shot by design)
  would need a 600 MB installer in memory.

Key and signature texts carry a prefix, so a private key pasted where the
public key belongs is recognised and refused instead of being committed and
shipped as if it were public.

`cryptography` is imported inside the functions that need it, so a caller can
fail closed with its own message when it's missing.
"""
import base64
import binascii
import re

PUBLIC_PREFIX = "ed25519-public:"
PRIVATE_PREFIX = "ed25519-private:"
MESSAGE_HEADER = "baihe-studio-release-signature-v1"
KEY_BYTES = 32
SIGNATURE_BYTES = 64
MAX_KEY_FILE_BYTES = 4096

_EXE_NAME_RE = re.compile(r"BaiheStudio-Setup-[0-9A-Za-z.+-]{1,100}\.exe")
_HEX64_RE = re.compile(r"[0-9a-f]{64}")


class SignatureFormatError(ValueError):
    """A key or signature text that isn't in the expected form. The message
    names the problem only, never the text."""


def signed_message(exe_name: str, sha256_hex: str) -> bytes:
    """The exact bytes that are signed. Both parts are checked so neither can
    add a line of its own to the statement."""
    if not _EXE_NAME_RE.fullmatch(exe_name or "") or not _HEX64_RE.fullmatch(sha256_hex or ""):
        raise SignatureFormatError("not an installer name and a lower-case SHA-256")
    return f"{MESSAGE_HEADER}\nname: {exe_name}\nsha256: {sha256_hex}\n".encode("ascii")


def _decode(text: str, size: int, what: str) -> bytes:
    try:
        raw = base64.b64decode(text.encode("ascii"), validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError):
        raise SignatureFormatError(f"the {what} isn't valid base64") from None
    if len(raw) != size:
        raise SignatureFormatError(f"the {what} has the wrong length")
    return raw


def _key_lines(text: str) -> list:
    return [line.strip() for line in str(text or "").splitlines()
            if line.strip() and not line.strip().startswith("#")]


def parse_public_key(text: str):
    """The raw 32-byte public key from a key file's text, or None when the
    text holds no key (blank or only `#` comments). Anything else that isn't
    exactly one public key raises, so callers can fail closed."""
    lines = _key_lines(text)
    if not lines:
        return None
    if len(lines) != 1:
        raise SignatureFormatError("more than one key line")
    line = lines[0]
    if line.startswith(PRIVATE_PREFIX):
        raise SignatureFormatError("it holds a private key")
    if not line.startswith(PUBLIC_PREFIX):
        raise SignatureFormatError("it isn't an ed25519 public key")
    return _decode(line[len(PUBLIC_PREFIX):], KEY_BYTES, "public key")


def parse_private_key(text: str) -> bytes:
    """The raw 32-byte private key (seed) from its text form."""
    lines = _key_lines(text)
    if len(lines) != 1 or not lines[0].startswith(PRIVATE_PREFIX):
        raise SignatureFormatError("it isn't an ed25519 private key")
    return _decode(lines[0][len(PRIVATE_PREFIX):], KEY_BYTES, "private key")


def parse_signature(data: bytes) -> bytes:
    """The raw 64-byte signature from a `.sig` file's bytes (base64, surrounding
    whitespace allowed)."""
    try:
        text = bytes(data).decode("ascii")
    except UnicodeDecodeError:
        raise SignatureFormatError("the signature isn't text") from None
    return _decode("".join(text.split()), SIGNATURE_BYTES, "signature")


def format_public_key(raw: bytes) -> str:
    return PUBLIC_PREFIX + base64.b64encode(raw).decode("ascii")


def format_private_key(raw: bytes) -> str:
    return PRIVATE_PREFIX + base64.b64encode(raw).decode("ascii")


def format_signature(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii") + "\n"


def generate() -> tuple:
    """A new key pair as (private text, public text)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                             serialization.NoEncryption())
    return format_private_key(seed), public_key_for(seed)


def public_key_for(private_raw: bytes) -> str:
    """The public key text that belongs to a raw private key."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    public = Ed25519PrivateKey.from_private_bytes(private_raw).public_key()
    return format_public_key(public.public_bytes(serialization.Encoding.Raw,
                                                 serialization.PublicFormat.Raw))


def sign(private_raw: bytes, exe_name: str, sha256_hex: str) -> bytes:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    return Ed25519PrivateKey.from_private_bytes(private_raw).sign(
        signed_message(exe_name, sha256_hex))


def verify(public_raw: bytes, signature: bytes, exe_name: str, sha256_hex: str) -> bool:
    """True only when `signature` is the public key's owner's signature of this
    installer name and hash."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    message = signed_message(exe_name, sha256_hex)
    try:
        Ed25519PublicKey.from_public_bytes(public_raw).verify(signature, message)
    except (InvalidSignature, ValueError):
        return False
    return True
