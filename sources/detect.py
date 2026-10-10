"""
sources/detect.py -- reading a response and naming what happened
(the failure-reason taxonomy).

Only reports what the response actually shows: status code, headers,
final URL, page title, visible text length, and a handful of well-known
markers. It never tries to get *past* anything it detects -- it names it,
and the caller decides which tier to try next (or hands off to the user).
"""

import re
from typing import Optional

from .models import FailureReason

# No "<" inside the tag or the title text: a lazy `(.*?)</title>` is
# quadratic on many unclosed "<title>" tags (pasted page source can be 5 MB).
_TITLE_RE = re.compile(r"<title[^<>]*>([^<]*)</title>", re.I)
# Script/style/noscript blocks are cut by `_strip_blocks` with str.find: a
# lazy `<script\b.*?</script>` regex is quadratic on an unclosed <script>,
# and this runs on every fetched page. No "<" inside a tag for the same
# reason: `<[^>]+>` rescans to the end from every "<" when no ">" follows.
_TAG_RE = re.compile(r"<[^<>]+>")
_BLOCK_OPEN_RE = re.compile(r"<(script|style|noscript)\b", re.I)
# Every threshold classify() compares against is a few thousand characters,
# so text past this cap can't change a verdict; it only costs time.
_MAX_VISIBLE_TEXT = 512 * 1024

# Cloudflare's managed/JS challenge. `cf-mitigated: challenge` is the
# authoritative signal; the body markers catch older challenge pages.
_CF_BODY_MARKERS = ("cf-chl-", "challenge-platform", "cf_chl_opt", "__cf_chl",
                    "<title>just a moment...</title>", "attention required! | cloudflare")
_BOT_CHALLENGE_MARKERS = ("g-recaptcha", "h-captcha", "hcaptcha.com/1/api.js",
                          "recaptcha/api.js", "cf-turnstile", "verify you are human",
                          "are you a robot", "人机验证", "安全验证", "滑动验证",
                          "보안문자", "로봇이 아닙니다")
_GEO_MARKERS = ("not available in your country", "not available in your region",
                "unavailable in your region", "this content is not available in your location",
                "해외 ip", "해외에서는", "국내에서만", "该地区", "所在地区无法", "您所在的地区",
                "お住まいの地域", "地域からはご利用")
_AUTH_MARKERS = ('type="password"', "type='password'", "please log in", "please sign in",
                 "login required", "请登录", "請登入", "로그인이 필요", "ログインしてください")
_PURCHASE_MARKERS = ("purchase this chapter", "buy this chapter", "unlock this chapter",
                     "购买本章", "購買本章", "付费章节", "이용권", "구매하기", "購入する")
_COOKIE_MARKERS = ("enable cookies", "cookies are disabled", "请启用cookie", "쿠키를 허용")
_JS_REQUIRED_MARKERS = ("enable javascript", "javascript is required", "requires javascript",
                        "you need to enable javascript", "请开启javascript", "请启用javascript",
                        "자바스크립트를 활성화")
_CRYPTO_MARKERS = ("crypto-js", "cryptojs.aes", "crypto.aes.decrypt", "aes.decrypt(")
_VUE_MARKERS = ("v-if=", "v-for=", "{{ ", "vue.min.js", "vue.js", "__vue__", "data-v-")
_DRM_MARKERS = ("requestmediakeysystemaccess", "com.widevine.alpha", "com.microsoft.playready",
                "com.apple.fps", "encrypted-media")

# A page the *person's own browser* has already machine-translated. This
# is not a failure -- the page loaded fine -- but it quietly ruins an
# extraction, because the translator REPLACES the source text in the DOM
# rather than annotating it. Confirmed directly against a real Google
# translation: the original Japanese was gone from the page afterwards,
# so extracting would hand this app English text to translate as though
# it were the original. It matters most for the user-assisted tier, where
# the HTML comes from the person's own browser.
#
# Each marker below was chosen from a real before/after diff of a live
# translation, keeping only the ones that appear *because* of it:
#   - `translated-ltr`/`translated-rtl` land on <html> when a translation
#     is applied (Chrome's built-in translate and the website widget
#     both set it).
#   - `goog-gt-tt`/`goog-gt-vt` are the tooltip and viewer elements
#     injected at translation time.
#   - Google rewrites every translated text node into nested
#     `<font style="vertical-align: inherit;">` wrappers.
# Deliberately NOT used: a bare `skiptranslate` class or a
# `google_translate_element` div. Both are present when the widget is
# merely embedded and idle, so matching them would report an untranslated
# page as translated.
_TRANSLATED_MARKERS = (
    ('class="translated-ltr"', "Google Translate (page marked translated-ltr)"),
    ("class='translated-ltr'", "Google Translate (page marked translated-ltr)"),
    ('class="translated-rtl"', "Google Translate (page marked translated-rtl)"),
    ("class='translated-rtl'", "Google Translate (page marked translated-rtl)"),
    ('id="goog-gt-tt"', "Google Translate (tooltip element present)"),
    ('id="goog-gt-vt"', "Google Translate (viewer element present)"),
    ("_msttexthash", "Microsoft/Edge Translator (text hashes on elements)"),
    ("_mstmutation", "Microsoft/Edge Translator (mutation markers)"),
)
# Google's own rewrite of each translated text node. Counted rather than
# matched once: a single stray <font> tag is ordinary old HTML, but a
# page full of vertical-align:inherit font wrappers is a translation.
_GT_FONT_RE = re.compile(r"<font[^>]*vertical-align:\s*inherit", re.I)
_GT_FONT_MIN = 2


def page_title(html: str) -> str:
    m = _TITLE_RE.search(html or "")
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def _strip_blocks(html: str) -> str:
    lower = html.lower()
    parts = []
    pos = 0
    while True:
        m = _BLOCK_OPEN_RE.search(html, pos)
        if not m:
            parts.append(html[pos:])
            break
        parts.append(html[pos:m.start()])
        # An unclosed block runs to the end of the page, as a browser treats it.
        close = lower.find("</" + m.group(1).lower(), m.end())
        if close < 0:
            break
        end = lower.find(">", close)
        if end < 0:
            break
        parts.append(" ")
        pos = end + 1
    return "".join(parts)


def visible_text(html: str) -> str:
    text = _strip_blocks(html or "")[:_MAX_VISIBLE_TEXT]
    text = _TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def machine_translated(html: str) -> list:
    """Which browser translator, if any, has already rewritten this page.

    Returns a list of plain descriptions (empty when the page looks
    untouched). This is deliberately NOT a `FailureReason`: the page
    loaded perfectly well, and nothing here should stop or escalate the
    access ladder. It is a warning, because the translator has replaced
    the original text and anything extracted from this page would be the
    translation rather than the source.
    """
    lower = (html or "").lower()
    found = []
    for marker, description in _TRANSLATED_MARKERS:
        if marker in lower and description not in found:
            found.append(description)
    if len(_GT_FONT_RE.findall(html or "")) >= _GT_FONT_MIN:
        description = "Google Translate (text rewritten into <font> wrappers)"
        if description not in found:
            found.append(description)
    return found


def _lower_headers(headers) -> dict:
    return {str(k).lower(): str(v) for k, v in (headers or {}).items()}


def classify(status: Optional[int], headers=None, body: str = "", url: str = "",
             final_url: str = "") -> list:
    """
    Returns the failure reasons this response shows, most specific first,
    or [] if it looks like ordinary, usable content.

    More than one reason can apply at once: a manhuaku-shaped page is both
    ENCRYPTED_RESOURCE and JAVASCRIPT_REQUIRED, and the ladder needs both
    facts (don't decode it; do try a real browser).
    """
    h = _lower_headers(headers)
    body = body or ""
    lower = body.lower()
    reasons = []

    # --- Active challenges first: these change what's allowed next. ---
    if h.get("cf-mitigated", "").lower() == "challenge" or (
            status in (403, 429, 503) and any(m in lower for m in _CF_BODY_MARKERS)):
        return [FailureReason.CLOUDFLARE_CHALLENGE]
    if any(m in lower for m in _BOT_CHALLENGE_MARKERS) and len(visible_text(body)) < 3000:
        return [FailureReason.BOT_CHALLENGE]

    if status == 451 or any(m in lower for m in _GEO_MARKERS):
        reasons.append(FailureReason.GEO_RESTRICTION)
    if status == 429:
        reasons.append(FailureReason.RATE_LIMIT)
    if status == 401:
        reasons.append(FailureReason.AUTHENTICATION_REQUIRED)
    if status == 402:
        reasons.append(FailureReason.PURCHASE_REQUIRED)
    if status == 403 and not reasons:
        reasons.append(FailureReason.ACCESS_DENIED)
    if status in (404, 410) and not reasons:
        reasons.append(FailureReason.NOT_FOUND)
    if status is not None and status >= 500 and not reasons:
        reasons.append(FailureReason.SERVER_ERROR)
    if status is not None and 400 <= status < 500 and not reasons:
        reasons.append(FailureReason.HTTP_ERROR)

    if status is not None and status >= 400:
        return reasons

    # --- A 2xx/3xx page: check what it actually contains. ---
    final = (final_url or url or "").lower()
    text = visible_text(body)
    if re.search(r"/(login|signin|sign-in|member/login|passport)\b", final) and \
            not re.search(r"/(login|signin|sign-in|member/login|passport)\b", (url or "").lower()):
        reasons.append(FailureReason.AUTHENTICATION_REQUIRED)
    elif any(m in lower for m in _AUTH_MARKERS) and len(text) < 1500:
        reasons.append(FailureReason.AUTHENTICATION_REQUIRED)
    if any(m in lower for m in _PURCHASE_MARKERS):
        reasons.append(FailureReason.PURCHASE_REQUIRED)
    if any(m in lower for m in _COOKIE_MARKERS) and len(text) < 1500:
        reasons.append(FailureReason.COOKIE_REQUIRED)
    if any(m in lower for m in _DRM_MARKERS):
        reasons.append(FailureReason.DRM_DETECTED)

    if any(m in lower for m in _CRYPTO_MARKERS) and any(m in lower for m in _VUE_MARKERS):
        # Site-side decryption of its own content in JS. Named, never run here.
        reasons.append(FailureReason.ENCRYPTED_RESOURCE)
        reasons.append(FailureReason.JAVASCRIPT_REQUIRED)

    if FailureReason.JAVASCRIPT_REQUIRED not in reasons:
        from page_fetch import looks_like_unrendered_shell
        shell = looks_like_unrendered_shell(body, text)
        js_notice = any(m in lower for m in _JS_REQUIRED_MARKERS)
        if shell["is_shell"] or (js_notice and len(text) < 600):
            reasons.append(FailureReason.EMPTY_SPA_SHELL)
            reasons.append(FailureReason.JAVASCRIPT_REQUIRED)
    return reasons


def evidence(status, headers, body, url, final_url="") -> dict:
    """The facts diagnostics records per attempt -- observed, not inferred."""
    keep = ("server", "cf-mitigated", "cf-ray", "content-type", "location", "retry-after",
            "x-cache", "via")
    h = _lower_headers(headers)
    return {
        "http_status": status,
        "final_url": final_url or url,
        "page_title": page_title(body),
        "text_length": len(visible_text(body)),
        "headers": {k: h[k] for k in keep if k in h},
        # Recorded as a fact per attempt, like everything else here, so a
        # puzzling extraction ("why is my Chinese novel in English?") has
        # the answer sitting in its own diagnostics.
        "machine_translated": machine_translated(body),
        # `looks_like_unrendered_shell` already works out *how* confident
        # it is and *why*, and `classify` above throws both away, keeping
        # only its boolean. They are the closest thing this project has to
        # a "is the text really in the DOM?" measurement, so they are kept
        # here as facts rather than recomputed by whoever wants them.
        **_shell_evidence(body),
    }


def _shell_evidence(body: str) -> dict:
    try:
        from page_fetch import looks_like_unrendered_shell
        shell = looks_like_unrendered_shell(body, visible_text(body))
    except Exception:
        return {"shell_confidence": None, "shell_reasons": []}
    return {"shell_confidence": shell.get("confidence"),
            "shell_reasons": list(shell.get("reasons") or [])}
