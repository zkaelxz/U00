"""Scrubbing secrets (API keys, bearer tokens, URL userinfo) out of error text
before it is shown, stored or logged. No provider code; `shared` re-exports
redact_secrets, so engines and callers keep importing it from there."""

import re


# Matches a raw API key/token sitting in an error string -- a query
# param ("...&key=AIzaSy..."), an Authorization header dump, or a
# provider key by its own prefix (sk-... for Claude/OpenAI-compatible,
# AIza... for Google). Belt-and-suspenders alongside sending keys as
# headers rather than URL params: a raise_for_status() failure's message
# includes the request URL, and that message is what gets stored on
# dramas.last_translate_errors and shown in the UI.
_SECRET_PATTERNS = [
    re.compile(r'([?&]key=)[^&\s"\']+', re.IGNORECASE),
    re.compile(r'(authorization["\']?\s*[:=]\s*["\']?(?:Bearer\s+)?)[A-Za-z0-9_\-\.]{10,}',
               re.IGNORECASE),
    re.compile(r'\bsk-[A-Za-z0-9_-]{10,}\b'),
    re.compile(r'\bAIza[A-Za-z0-9_-]{10,}\b'),
    # Hugging Face user access tokens: hf_ + 34 letters/digits today. The
    # 20-char floor keeps ordinary identifiers like "hf_model" untouched.
    re.compile(r'\bhf_[A-Za-z0-9]{20,}\b'),
    # Groq keys: gsk_ + ~52 letters/digits.
    re.compile(r'\bgsk_[A-Za-z0-9]{20,}\b'),
    # Notion integration secrets: ntn_ (current) or secret_ (older) + 40+
    # letters/digits. Kept although the integration is gone: a stored or pasted
    # Notion token must still be scrubbed from error text. Same floor idea as
    # hf_ above, so words like "secret_key" or "ntn_status" are left alone.
    re.compile(r'\b(?:ntn|secret)_[A-Za-z0-9]{20,}\b'),
    # DeepL keys: a UUID, with ":fx" on Free-plan keys. A bare UUID is
    # only redacted with the ":fx" suffix or after "DeepL-Auth-Key", so
    # the app's own UUID ids stay readable in logs.
    re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:fx\b',
               re.IGNORECASE),
    re.compile(r'(DeepL-Auth-Key\s+)[A-Za-z0-9:\-]{10,}', re.IGNORECASE),
    # Google OAuth client secrets (sign-in, step 134): GOCSPX- + ~28 chars.
    re.compile(r'\bGOCSPX-[A-Za-z0-9_-]{10,}'),
    # GitHub tokens: ghp_/gho_/ghu_/ghs_/ghr_ and fine-grained github_pat_.
    re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})'),
    # Baihe's own browser-extension device tokens: baihe_dt_ + 43 characters.
    re.compile(r'\bbaihe_dt_[A-Za-z0-9_-]{20,}'),
    # Ollama API keys (a member's own, sent to ollama.com). Ollama doesn't
    # publish a format, so both likely shapes are covered: an "ollama_"
    # prefix, and 32 hex characters, a dot and a long tail.
    re.compile(r'\bollama_[A-Za-z0-9_-]{20,}'),
    re.compile(r'\b[0-9a-f]{32}\.[A-Za-z0-9_-]{20,}', re.IGNORECASE),
    # Discord webhook URLs: the id/token path is the secret.
    re.compile(r'(discord(?:app)?\.com/api/(?:v\d+/)?webhooks/)[^\s"\'<>]+', re.IGNORECASE),
]


# scheme://user:pass@host or scheme://token@host: the userinfo is the secret,
# host and path stay readable. Greedy so a raw "@" inside the password is
# still covered.
_URL_USERINFO_PATTERN = re.compile(r'(\b[a-z][a-z0-9+.-]*://)[^\s/?#"\'<>]+@', re.IGNORECASE)


def redact_secrets(text: str) -> str:
    """Strips anything that looks like an API key or bearer token out of
    an error string before it's shown in the UI, stored on the drama, or
    written to the log file."""
    if not text:
        return text
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: m.group(1) + "[REDACTED]" if m.groups() else "[REDACTED]", text)
    # After the token patterns: userinfo they already replaced stays as is.
    return _URL_USERINFO_PATTERN.sub(
        lambda m: m.group(0) if "[REDACTED]" in m.group(0) else m.group(1) + "***@", text)
