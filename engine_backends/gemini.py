"""Gemini engine, its rate-limit status text and free-tier limits."""

import time
from .prompts import build_batch_user_message, build_stable_system_text
from .shared import (
    ContentModerationBlocked,
    FreeTierDailyLimitReached,
    _MAX_THROTTLE_WAIT_SECONDS,
    _add_usage,
    _cancellable_sleep,
    _empty_usage,
    gemini_usage,
    read_json_capped,
    request_translations_with_retry,
)


# Real quota headers Gemini's API sometimes returns (confirmed via Google's
# own docs and independent API guides) -- reliable on a 429 response, not
# guaranteed on every successful one depending on the installed SDK version.
_GEMINI_RATE_HEADERS = {
    "limit_requests": "x-ratelimit-limit-requests",
    "remaining_requests": "x-ratelimit-remaining-requests",
    "remaining_tokens": "x-ratelimit-remaining-tokens",
    "reset_requests": "x-ratelimit-reset-requests",
}


def _parse_gemini_rate_headers(headers) -> dict:
    """{} if none of the expected headers are present on this response --
    the caller falls back to its own self-tracked RPM/RPD/TPM counts."""
    out = {}
    for field, header_name in _GEMINI_RATE_HEADERS.items():
        v = headers.get(header_name) if headers else None
        if v is not None:
            out[field] = v
    return out


def gemini_rate_status_text(engine) -> str:
    """The small persistent note shown near the engine picker / job
    progress while a free-tier Gemini engine is active. None if there's
    nothing to show yet (no request made this session, or not a free-tier
    Gemini engine)."""
    status = getattr(engine, "rate_status", None)
    if not status:
        return None
    note = (f"{status['rpm_used']}/{status['rpm_limit']} req/min · "
            f"{status['rpd_used']}/{status['rpd_limit']} req/day · "
            f"{status['tpm_used']:,}/{status['tpm_limit']:,} tokens/min")
    if status["source"] == "header":
        note += " (Google's own reported quota)"
    limit_header = status.get("limit_requests")
    if limit_header is not None:
        try:
            if int(limit_header) != status["rpm_limit"]:
                note += " -- ⚠️ Google's real limit differs from this app's table; may need updating"
        except (TypeError, ValueError):
            pass
    return note


def progress_message_with_rate_status(engine, frac: float, base: str = None) -> str:
    """A job's progress message, with the free-tier Gemini rate-status note
    appended when there's one to show. `base` defaults to the plain
    "Translating..." text every other engine already shows."""
    message = base or f"Translating... {frac*100:.0f}%"
    if getattr(engine, "free_tier", False):
        note = gemini_rate_status_text(engine)
        if note:
            return f"{message} — {note}"
    return message


# ---------------------------------------------------------------------------
# Gemini (Google) -- cheap, strong multilingual, native generateContent REST call
# ---------------------------------------------------------------------------

class GeminiEngine:
    """Uses the plain generateContent REST endpoint rather than the
    google-genai SDK -- no extra dependency needed, and it's a simple enough
    API that the SDK doesn't buy much here. The key goes in the
    x-goog-api-key header, never the URL, so it cannot reach logs or stored
    error text."""
    name = "gemini"
    supports_reference = True

    def __init__(self, api_key: str, model: str = "gemini-flash-lite-latest",
                 free_tier: bool = False):
        self.api_key = api_key
        self.model = model
        self.last_usage = _empty_usage()
        # Free-tier Gemini keys hard-error past a per-model RPM/RPD limit,
        # plus a shared TPM limit across every model -- self-pacing
        # client-side is cheaper than handling 429s. Paid keys have no such
        # limit, so this only applies here.
        self.free_tier = free_tier
        self._free_tier_request_times = []       # 60s window, for RPM
        self._free_tier_daily_request_times = []  # 24h window, for RPD
        self._free_tier_token_counts = []         # [(timestamp, tokens)], 60s window, for TPM
        # Populated after the first request when free_tier is set --
        # gemini_rate_status_text() reads this to show the small
        # persistent rate-status note near the engine picker / job progress.
        self.rate_status = None

    def _prune_free_tier_windows(self, now):
        self._free_tier_request_times = [
            t for t in self._free_tier_request_times if now - t < 60]
        self._free_tier_daily_request_times = [
            t for t in self._free_tier_daily_request_times if now - t < 86400]
        self._free_tier_token_counts = [
            (t, n) for t, n in self._free_tier_token_counts if now - t < 60]

    def _throttle_for_free_tier(self):
        if not self.free_tier:
            return
        limits = gemini_free_tier_limits_for(self.model)
        now = time.monotonic()
        self._prune_free_tier_windows(now)

        # Whichever ceiling is closest to being hit decides how long to
        # wait -- RPM and TPM (60s windows) or RPD (24h window). The
        # upcoming request's own token count isn't known yet, so TPM can
        # only react to what past requests have already used.
        wait = 0.0
        if len(self._free_tier_request_times) >= limits["rpm"]:
            wait = max(wait, 60 - (now - self._free_tier_request_times[0]))
        if len(self._free_tier_daily_request_times) >= limits["rpd"]:
            wait = max(wait, 86400 - (now - self._free_tier_daily_request_times[0]))
        tokens_in_window = sum(n for _, n in self._free_tier_token_counts)
        if self._free_tier_token_counts and tokens_in_window >= GEMINI_FREE_TIER_TPM:
            wait = max(wait, 60 - (now - self._free_tier_token_counts[0][0]))

        if wait > _MAX_THROTTLE_WAIT_SECONDS:
            raise FreeTierDailyLimitReached(
                "The free-tier daily request limit for this Gemini model was reached. "
                "Try again tomorrow, or use a paid key or another engine.")
        if wait > 0:
            _cancellable_sleep(wait)
            now = time.monotonic()
            self._prune_free_tier_windows(now)

        self._free_tier_request_times.append(now)
        self._free_tier_daily_request_times.append(now)

    def _update_rate_status(self, headers, usage: dict):
        """Refreshes self.rate_status after a request -- self-tracked
        RPM/RPD/TPM counts always, plus Google's own reported quota
        headers layered on top when the response included them (reliable
        on a 429, not guaranteed on every 200 depending on SDK version)."""
        now = time.monotonic()
        self._free_tier_token_counts.append(
            (now, usage.get("input_tokens", 0) + usage.get("output_tokens", 0)))
        self._prune_free_tier_windows(now)
        limits = gemini_free_tier_limits_for(self.model)
        header_info = _parse_gemini_rate_headers(headers)
        status = {
            "source": "header" if header_info else "estimated",
            "rpm_used": len(self._free_tier_request_times),
            "rpm_limit": limits["rpm"],
            "rpd_used": len(self._free_tier_daily_request_times),
            "rpd_limit": limits["rpd"],
            "tpm_used": sum(n for _, n in self._free_tier_token_counts),
            "tpm_limit": GEMINI_FREE_TIER_TPM,
        }
        status.update(header_info)
        self.rate_status = status

    def build_request_body(self, context: dict, numbered: str) -> dict:
        """One translation request's generateContent body -- shared by
        translate_batch and bulk mode's Batch API submission."""
        return {
            "systemInstruction": {"parts": [{"text": build_stable_system_text(context)}]},
            "contents": [{"parts": [{"text": build_batch_user_message(context, numbered)}]}],
        }

    def translate_batch(self, zh_lines, context: dict):
        import requests
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model}:generateContent")
        self.last_usage = _empty_usage()

        def call_model(numbered):
            self._throttle_for_free_tier()
            resp = requests.post(url, headers={"x-goog-api-key": self.api_key},
                                 json=self.build_request_body(context, numbered), timeout=120,
                                 stream=True)
            data = read_json_capped(resp, 120)
            usage = gemini_usage(data.get("usageMetadata"))
            _add_usage(self.last_usage, usage)
            if self.free_tier:
                self._update_rate_status(resp.headers, usage)
            # A real safety block returns either no candidates at
            # all (blocked before generation even started -- the reason is
            # in promptFeedback.blockReason) or a candidate whose
            # finishReason is SAFETY/PROHIBITED_CONTENT with no content --
            # both shapes previously raised a bare KeyError/IndexError from
            # the raw indexing below. Real, documented finishReason values,
            # not a guess.
            candidates = data.get("candidates")
            if not candidates:
                reason = (data.get("promptFeedback") or {}).get(
                    "blockReason", "blocked with no reason given")
                raise ContentModerationBlocked("gemini", reason)
            finish_reason = candidates[0].get("finishReason")
            if finish_reason in ("SAFETY", "PROHIBITED_CONTENT"):
                raise ContentModerationBlocked("gemini", finish_reason)
            return candidates[0]["content"]["parts"][0]["text"].strip()

        return request_translations_with_retry(zh_lines, context.get("speaker_labels"), call_model,
                                                line_ids=context.get("line_ids"), engine_name="gemini")


# Free-tier limits, confirmed against ai.google.dev/gemini-api/docs/rate-limits
# in September 2026 -- the original "~10 requests/minute on Flash" note
# was a full year stale, didn't distinguish Flash from Flash-Lite, and didn't
# mention the shared token ceiling at all. Three independent limits, not one.
GEMINI_FREE_TIER_LIMITS = {
    "flash-lite": {"rpm": 15, "rpd": 1000},
    "flash": {"rpm": 10, "rpd": 250},
}
GEMINI_FREE_TIER_DEFAULT_LIMITS = {"rpm": 10, "rpd": 250}
GEMINI_FREE_TIER_TPM = 250_000

# Removed from the free tier entirely in April 2026 -- a free-tier key can no
# longer reach it at all, regardless of RPM/RPD/TPM standing.
GEMINI_FREE_TIER_UNAVAILABLE_MODELS = {"gemini-pro-latest"}


def gemini_free_tier_limits_for(model: str) -> dict:
    """{"rpm": int, "rpd": int} for a given Gemini model under the free
    tier. Checked in this order since "flash-lite" also contains "flash"."""
    model = model or ""
    if "flash-lite" in model:
        return GEMINI_FREE_TIER_LIMITS["flash-lite"]
    if "flash" in model:
        return GEMINI_FREE_TIER_LIMITS["flash"]
    return GEMINI_FREE_TIER_DEFAULT_LIMITS
