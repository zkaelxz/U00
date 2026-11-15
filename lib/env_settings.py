"""The settings that exist only as environment variables or keys-file (.env) lines, with a plain-words label each.

Names only: a value is never read here. `from_file` marks the ones the app also reads from the
data folder's .env; the rest are read from the program's environment alone, so a line in .env
does nothing for them.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class EnvSetting:
    name: str
    label: str
    group: str
    from_file: bool


# Engine keys, the Ollama address and the monthly cap are left out: Settings already shows them.
ENV_SETTINGS: tuple[EnvSetting, ...] = (
    EnvSetting("BAIHE_PUBLIC_URL", "Public address (https)", "Sign-in and access", True),
    EnvSetting("BAIHE_GOOGLE_CLIENT_ID", "Google sign-in client ID", "Sign-in and access", True),
    EnvSetting("BAIHE_GOOGLE_CLIENT_SECRET", "Google sign-in client secret", "Sign-in and access", True),
    EnvSetting("BAIHE_MEMBER_KEY_SECRET", "Secret that protects members' own keys", "Sign-in and access", True),
    EnvSetting("BAIHE_API_ALLOW_KEY_WRITES", "Allow saving keys in Settings", "Sign-in and access", True),
    EnvSetting("BAIHE_REMOTE_IP_CHECK_URL", "Address used by the public address check", "Sign-in and access", True),
    EnvSetting("BAIHE_JELLYFIN_API_KEY", "Jellyfin key", "Connections", True),
    EnvSetting("BAIHE_NTFY_TOPIC_URL", "ntfy topic address", "Connections", True),
    EnvSetting("BAIHE_DISCORD_WEBHOOK_URL", "Discord webhook address", "Connections", True),
    EnvSetting("BAIHE_NTFY_ALLOW_LOCAL", "Allow an ntfy address on this network", "Connections", True),
    EnvSetting("BAIHE_UPDATE_REPO", "Where updates are fetched from", "Updates", True),
    EnvSetting("BAIHE_FEATURES_OFF", "Areas switched off (Jellyfin, Sources, extension)", "Areas", True),
    EnvSetting("BAIHE_API_HOST", "Address the server listens on", "Server", False),
    EnvSetting("BAIHE_API_PORT", "Server port", "Server", False),
    EnvSetting("BAIHE_API_HOUSEHOLD_PORT", "Household port", "Server", False),
    EnvSetting("BAIHE_API_ENV", "Development mode for the server", "Server", False),
    EnvSetting("BAIHE_API_CORS_ORIGINS", "Allowed origins in development", "Server", False),
    EnvSetting("BAIHE_API_AUTH", "Sign-in required", "Server", False),
    EnvSetting("BAIHE_API_COOKIE_SECURE", "Secure sign-in cookie", "Server", False),
    EnvSetting("BAIHE_API_BACKGROUND", "Background services", "Server", False),
    EnvSetting("BAIHE_API_SERVE_FRONTEND", "Serve the web app", "Server", False),
    EnvSetting("BAIHE_API_SESSION_IDLE_DAYS", "Days before an idle sign-in ends", "Server", False),
    EnvSetting("BAIHE_API_SESSION_MAX_DAYS", "Days before any sign-in ends", "Server", False),
    EnvSetting("BAIHE_DATA_DIR", "Data folder", "Folders and limits", False),
    EnvSetting("BAIHE_AUDIO_SEP_MODEL_DIR", "Voice-separation models folder", "Folders and limits", False),
    EnvSetting("BAIHE_BROWSER_PATH", "Browser program for fetching pages", "Folders and limits", False),
    EnvSetting("BAIHE_MAX_UPLOAD_MB", "Upload size limit (MB)", "Folders and limits", False),
    EnvSetting("BAIHE_NOTIFY_DISABLED", "Switch every notification off", "Connections", False),
)
