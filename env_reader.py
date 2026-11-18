"""Reads the app's .env file (where keys and endpoint settings live) for every layer."""
import os

import portable


def default_env_path() -> str:
    # The project folder for a source checkout; the per-user data folder
    # for an installed copy, so keys never sit in the program files an
    # update replaces (portable.data_dir()).
    return os.path.join(portable.data_dir(), ".env")


def file_encoding(env_path: str) -> str:
    """utf-16 for a .env with a UTF-16 byte-order mark (Notepad's "Unicode",
    PowerShell 5's `>` and Out-File), else utf-8-sig. Read as UTF-8, every
    key in such a file is missed and a save mixes two encodings."""
    try:
        with open(env_path, "rb") as fh:
            head = fh.read(4)
    except OSError:
        return "utf-8-sig"
    # FF FE 00 00 is UTF-32-LE (PowerShell -Encoding UTF32): decoded as UTF-16 it
    # would be saved back with a NUL after every character, so it stays on the
    # byte-preserving UTF-8 path.
    utf16 = head[:2] in (b"\xff\xfe", b"\xfe\xff") and head != b"\xff\xfe\x00\x00"
    return "utf-16" if utf16 else "utf-8-sig"


def read_env_file(env_path: str | None = None) -> dict:
    """Parses the project's .env file: utf-8-sig (BOM-safe, since Notepad-saved
    .env files often carry one) or UTF-16 (file_encoding), skips comments/blank lines, strips
    surrounding quotes. Returns {} if the file is missing or malformed --
    a broken .env should never crash a caller.
    """
    env = {}
    if env_path is None:
        env_path = default_env_path()
    if os.path.exists(env_path):
        try:
            # A stray non-UTF-8 byte (a GBK comment) must not discard the keys after it.
            with open(env_path, encoding=file_encoding(env_path), errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
        except Exception:
            pass
    return env


def resolve_env_names(names, env_path: str | None = None) -> str | None:
    """Server-side only: the first non-empty value among `names`, from .env
    then real environment variables. Never return it over an API response."""
    env = read_env_file(env_path)
    for name in names:
        val = env.get(name) or os.environ.get(name)
        if val:
            return val
    return None
