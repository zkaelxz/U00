"""
sources/preflight.py -- "will this site work?", answered before anyone
commits to an import.

Every piece of the answer already existed; nothing here adds a new access
tier, a new extractor or a new network path. What was missing was a way
to *ask*: until now the only way to find out whether a site could be read
was to run a real import and see what came back. `front_door.preview`
comes close, but it answers "what is this page" -- not "would extracting
it actually work, and am I allowed to".

This composes the existing parts into one verdict:

  permission   `ladder.check_terms` -- reported, never raised past the
               caller, because "not allowed" is an answer, not a crash
  reachable    the real access ladder (static HTTP -> browser render),
               and which tier it took
  readable     the deterministic extractor plus `ai_extract.validate_novel`
               -- the same independent checks an import is judged by, so
               the preflight can't be kinder than the real thing
  trustworthy  duplicate-paragraph, short-fragment and link-text rates,
               i.e. "is this prose or is it navigation"
  navigable    whether next/previous/contents links were found, which is
               what decides if a whole series can be followed
  warnings     a page the person's own browser already machine-translated

**Why not ask Google Translate.** A well-known rule of thumb says that if
Google Translate can translate a site, a generic scraper can read it.
That is true, and for a good reason: both need the text to be real DOM
text rather than pixels in an image or glyphs on a canvas. But it is an
*indirect* measurement of something measured here directly and better --
`validate_novel` already scores exactly that property, offline, without
sending anyone's URLs to a third party, and it also answers the three
questions a translation check never could: is there a chapter list, is
this prose or chrome, and are we permitted to read it at all.

One fetch. The page is fetched once through the normal ladder and every
answer below is derived from that same response.
"""

from dataclasses import dataclass, field

from . import ai_extract as ax
from . import detect, front_door, profiles
from .models import SourceError, TermsProhibited

UNKNOWN = front_door.UNKNOWN


@dataclass
class Preflight:
    """What a preflight found. `ok` is the short answer; `lines` is the
    long one, in the order a person would want to read it."""
    url: str
    ok: bool = False
    verdict: str = ""
    permitted: bool = True
    reachable: bool = False
    content_type: str = UNKNOWN
    tier: str = ""
    adapter: str = None
    title: str = ""
    text_chars: int = 0
    images: int = 0
    confidence: str = ""
    problems: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    next_link: bool = False
    previous_link: bool = False
    toc_links: int = 0
    profile: str = ""
    lines: list = field(default_factory=list)

    def note(self, line: str):
        self.lines.append(line)

    def summary(self) -> str:
        return self.verdict


def _describe_profile(url: str, kind: str) -> str:
    try:
        domain = profiles.domain_of(url)
        version = profiles.active(domain, kind)
    except Exception:
        return ""
    if not version:
        return ""
    return (f"A saved extraction profile for {domain} is active "
            f"(v{version.get('version')}, from {version.get('origin')}).")


def _check_novel(result: Preflight, html: str, url: str):
    """The readability half: run the real deterministic tier and judge it
    with the same independent checks an import is judged by."""
    page = ax.PageModel(html, url)
    data = ax.deterministic_novel(page)
    ax.validate_novel(data, page)
    overall = data.get("overall") or {}
    result.confidence = overall.get("bucket") or ""
    result.text_chars = len(data.get("content") or "")
    result.problems = list(data.get("problems") or [])
    result.next_link = bool(data.get("next_url"))
    result.previous_link = bool(data.get("previous_url"))
    result.toc_links = len(data.get("toc_links") or [])
    result.profile = _describe_profile(url, "novel")

    if data.get("valid"):
        result.note(f"Read {result.text_chars:,} characters of chapter text "
                    f"({data.get('method')}), confidence {result.confidence}.")
    else:
        result.note(f"The text on this page didn't pass the same checks an import uses "
                    f"(confidence {result.confidence or 'none'}).")
    for problem in result.problems:
        result.note(f"  - {problem}")

    followers = []
    if result.next_link:
        followers.append("a next-chapter link")
    if result.previous_link:
        followers.append("a previous-chapter link")
    if result.toc_links:
        followers.append(f"{result.toc_links} contents link(s)")
    if followers:
        result.note("Found " + ", ".join(followers) + " -- a series can be followed from here.")
    else:
        result.note("No next/previous/contents links found, so chapters would have to be "
                    "added one URL at a time.")
    return bool(data.get("valid"))


def preflight(url: str, client=None, rendered_fetch=None) -> Preflight:
    """Fetch `url` once and report whether importing it would work.

    Never raises for an ordinary "no": a site whose terms prohibit
    automated access, an unreachable page and a page with no readable
    text are all *findings*, reported on the result. Only genuinely
    unexpected errors propagate.
    """
    url = (url or "").strip()
    result = Preflight(url=url)
    if not url:
        result.verdict = "No URL given."
        return result

    try:
        preview = front_door.preview(url, client=client, rendered_fetch=rendered_fetch)
    except TermsProhibited as e:
        # A written restriction is an answer, and the most important one
        # here -- it is reported rather than raised, so a preflight can
        # be run on anything without blowing up in the caller's face.
        result.permitted = False
        result.verdict = "This site's terms prohibit automated access, so it won't be imported."
        result.note(str(e))
        return result
    except SourceError as e:
        # Same principle for every other ordinary refusal -- a challenge
        # page, a login wall, a timeout. "I couldn't" is the answer this
        # was asked for, so it is reported the same way.
        reason = getattr(getattr(e, "reason", None), "value", "") or "couldn't be read"
        result.verdict = f"Couldn't check this page ({reason})."
        result.note(str(e))
        return result

    result.adapter = preview.adapter
    result.title = preview.title or ""
    result.content_type = preview.content_type
    result.images = preview.image_count or 0
    for note in preview.notes or []:
        result.note(note)

    if preview.adapter:
        # A registered adapter answers the question by existing: it was
        # built and tested against this site.
        result.ok = True
        result.permitted = True
        result.reachable = True
        result.tier = "dedicated adapter"
        result.verdict = f"Supported by the built-in {preview.platform or preview.adapter} adapter."
        return result

    ladder_result = preview.ladder
    result.reachable = bool(ladder_result is not None and ladder_result.ok)
    if ladder_result is not None:
        result.tier = getattr(ladder_result, "tier", "") or ""
    if not result.reachable:
        result.verdict = "Couldn't reach a readable page here."
        return result

    result.note(f"Reached the page over {result.tier or 'HTTP'}.")

    # A page the person's own browser already translated would hand this
    # app the translation as though it were the source.
    translators = detect.machine_translated(preview.html)
    if translators:
        result.warnings.append(
            "This page has already been translated by " + ", ".join(translators) +
            ". Turn the browser's page translation off for this site before importing, or "
            "the translation would be saved as the original text.")
        result.note(result.warnings[-1])

    if result.content_type == front_door.NOVEL:
        readable = _check_novel(result, preview.html, url)
        result.ok = readable
        result.verdict = ("Looks importable as a novel."
                          if readable else
                          "Reached the page, but its text didn't pass the import checks.")
    elif result.content_type == front_door.COMIC:
        result.profile = _describe_profile(url, "comic")
        result.ok = result.images >= 3
        result.note(f"Found {result.images} page-sized image(s).")
        result.verdict = ("Looks importable as a comic."
                          if result.ok else
                          "Reached the page, but not enough page-sized images to import.")
    elif result.content_type == front_door.VIDEO:
        result.ok = True
        result.verdict = "Looks like a video page -- downloads through yt-dlp."
    else:
        result.text_chars = preview.text_length or 0
        result.verdict = "Reached the page, but couldn't tell what kind of content it holds."

    if result.profile:
        result.note(result.profile)
    if result.warnings and result.ok:
        result.verdict += " (with a warning, below)"
    return result
