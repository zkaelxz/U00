"""
reader.py -- renders the interactive Reader view: raw text with ruby
annotations (pinyin/furigana) above each word, English translation
alongside, and click-to-define popups. All definition/reading data is
precomputed in Python and baked into the HTML as JSON, so the
in-browser interactivity (click a word -> see its definition) is pure
client-side JS with no round-trip back to the server needed.

The React Reader page shows it in an iframe (services/reader_service.py).
"""

import json
import html


THEMES = {
    "light": {"bg": "#fafafa", "fg": "#1a1a1a", "sub": "#444", "border": "#e5e5e5",
              "hover": "#ffe9a8", "active": "#ffd35c", "rt": "#888"},
    "sepia": {"bg": "#f4ecd8", "fg": "#3a3226", "sub": "#5b5040", "border": "#ddd0b4",
              "hover": "#e8d9a8", "active": "#dcc37a", "rt": "#8a7a5c"},
    "dark":  {"bg": "#1c1c1e", "fg": "#e8e8ea", "sub": "#b0b0b6", "border": "#3a3a3e",
              "hover": "#4a4320", "active": "#6b5d20", "rt": "#9a9aa0"},
}

FONT_STACKS = {
    "system": "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    "serif": "Georgia, 'Songti SC', 'SimSun', serif",
    "sans-serif": "'Helvetica Neue', Arial, 'PingFang SC', sans-serif",
    "monospace": "'SF Mono', Consolas, monospace",
}


def _json_for_script(value) -> str:
    """JSON that is safe inside an inline <script>: no `</script>`, `<!--`
    or HTML-significant characters, and no U+2028/U+2029 line terminators.
    The escapes are valid JSON/JS string escapes, so the value is unchanged."""
    return (json.dumps(value, ensure_ascii=False)
            .replace("&", "\\u0026").replace("<", "\\u003c")
            .replace(">", "\\u003e").replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))


def build_reader_html(lines, source_language: str, definitions: dict,
                       audio_data_uri: str = None, theme: str = "light",
                       font_size: int = 22, line_height: float = 2.4,
                       max_width: int = 1200, font: str = "system") -> str:
    """
    lines: list of Line-like objects with .zh (raw) and .en (translation)
    source_language: 'zh' | 'ja' | 'ko'
    definitions: {word: {"reading": str|None, "definitions": [str]}}
    audio_data_uri: optional base64 data: URI for this page's audio
    span, enabling click-to-seek (clicking a line jumps playback to
    that line's timestamp). Only embed audio for the CURRENT PAGE's
    span, not a whole multi-hour file -- base64 inflates size by ~33%,
    so a full-length file would bloat the page badly. Reader.py handles
    slicing the right span before calling this.
    """
    import segment

    # These land in <style>; coerce so a string can't inject CSS/HTML.
    font_size = int(font_size)
    line_height = float(line_height)
    max_width = int(max_width)

    rows_html = []
    missing_segmenter = None
    for ln in lines:
        try:
            segments = segment.segment_and_annotate(ln.zh, source_language)
        except ImportError as e:
            # The word splitter (jieba/pypinyin, sudachipy/pykakasi, kiwipiepy)
            # is an optional install: show the line unsplit rather than no page.
            missing_segmenter = missing_segmenter or e.name or "a word-splitting package"
            segments = None
        word_spans = [html.escape(ln.zh or "")] if segments is None else []
        for word, reading in segments or ():
            if not word.strip():
                word_spans.append(html.escape(word))
                continue
            safe_word = html.escape(word)
            key = html.escape(word, quote=True)  # dataset.word reads it back verbatim
            ruby = f"<rt>{html.escape(reading)}</rt>" if reading else ""
            word_spans.append(
                f'<ruby class="word" data-word="{key}" onclick="showDef(this)">'
                f'{safe_word}{ruby}</ruby>'
            )
        raw_html = "".join(word_spans)
        en_html = html.escape(ln.en or "")
        seek_btn = (f'<button class="seek-btn" onclick="seekTo({ln.start})" title="Play from here">▶</button>'
                    if audio_data_uri else "")
        rows_html.append(f"""
        <div class="line-row" data-start="{ln.start}" data-end="{ln.end}" id="ln{ln.idx}">
          {seek_btn}
          <div class="raw-text">{raw_html}</div>
          <div class="en-text">{en_html}</div>
        </div>""")

    defs_json = _json_for_script(definitions)
    segmenter_note = (
        f'<p class="segmenter-note">Word readings and lookups need '
        f'<code>{html.escape(missing_segmenter)}</code> (see Diagnostics).</p>'
        if missing_segmenter else "")
    t = THEMES.get(theme, THEMES["light"])
    c_bg, c_fg, c_sub = t["bg"], t["fg"], t["sub"]
    c_border, c_hover, c_active, c_rt = t["border"], t["hover"], t["active"], t["rt"]
    c_accent = "#7C5CBF" if theme != "dark" else "#A98BE8"
    c_accent_soft = "#EFEAFA" if theme != "dark" else "#2A2338"
    c_surface = "#FFFFFF" if theme != "dark" else "#1E1E24"
    font_stack = FONT_STACKS.get(font, FONT_STACKS["system"])
    en_size = max(12, int(font_size * 0.68))
    rt_size = max(9, int(font_size * 0.5))
    page_start_offset = lines[0].start if (audio_data_uri and lines) else 0.0
    audio_html = f'<audio id="player" controls style="width:100%; margin-bottom:12px;"><source src="{html.escape(audio_data_uri or "", quote=True)}"></audio>' if audio_data_uri else ""
    follow_html = ('<div id="followbar"><label><input type="checkbox" id="followchk" checked> '
                    'Follow along while playing</label>'
                    '<span id="followstatus" style="opacity:.7"></span></div>'
                    if audio_data_uri else "")

    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; form-action 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; media-src data: blob:; img-src data:">
<style>
  body {{ font-family: {font_stack}; margin: 0 auto; padding: 12px;
         max-width: {max_width}px;
         background: {c_bg}; color: {c_fg}; }}
  .line-row {{ display: flex; gap: 24px; align-items: flex-start;
              padding: 14px 0; border-bottom: 1px solid {c_border}; }}
  .raw-text {{ flex: 1.2; font-size: {font_size}px; line-height: {line_height}; }}
  .en-text {{ flex: 1; font-size: {en_size}px; line-height: 1.6; color: {c_sub};
             padding-top: 6px; }}
  ruby.word {{ cursor: pointer; border-radius: 4px; padding: 0 1px; }}
  ruby.word:hover {{ background: {c_hover}; }}
  ruby.word.active {{ background: {c_active}; }}
  rt {{ font-size: {rt_size}px; color: {c_rt}; user-select: none; }}
  .seek-btn {{ border: none; background: {c_border}; border-radius: 50%; width: 28px;
              height: 28px; cursor: pointer; flex-shrink: 0; margin-top: 10px;
              color: {c_fg}; }}
  .seek-btn:hover {{ background: {c_active}; }}
  .line-row.playing {{
      background: {c_accent_soft};
      box-shadow: inset 3px 0 0 {c_accent};
      border-radius: 4px;
  }}
  #followbar {{
      position: sticky; top: 0; z-index: 11; padding: 6px 10px;
      background: {c_surface}; border: 1px solid {c_border};
      border-radius: 8px; margin-bottom: 8px; font-size: 13px;
      display: flex; align-items: center; gap: 10px;
  }}
  #popup {{
    position: sticky; top: 0; background: #222; color: #fff;
    padding: 10px 14px; border-radius: 8px; margin-bottom: 10px;
    font-size: 14px; display: none; z-index: 10;
  }}
  #popup b {{ font-size: 16px; }}
  #popup .reading {{ color: #ffd35c; margin-left: 8px; }}
  #popup .defs {{ margin-top: 4px; color: #ddd; }}
</style>
</head>
<body>
  {audio_html}
  {follow_html}
  <div id="popup"></div>
  {segmenter_note}
  <div id="rows">{"".join(rows_html)}</div>

<script>
  const DEFS = {defs_json};
  // The embedded clip covers only this page, so its t=0 is the page's first
  // line. Absolute line timestamps need that offset added back.
  const PAGE_START_OFFSET = {page_start_offset};
  let activeEl = null;

  function showDef(el) {{
    if (activeEl) activeEl.classList.remove('active');
    el.classList.add('active');
    activeEl = el;

    const word = el.dataset.word;
    const entry = DEFS[word];
    const popup = document.getElementById('popup');

    // Built with textContent only: word, reading and definitions can be
    // LLM output and must never be parsed as HTML.
    popup.replaceChildren();
    const b = document.createElement('b');
    b.textContent = word;
    popup.appendChild(b);
    if (!entry) {{
      const span = document.createElement('span');
      span.className = 'reading';
      span.textContent = '(no definition available)';
      popup.appendChild(span);
    }} else {{
      if (entry.reading) {{
        const span = document.createElement('span');
        span.className = 'reading';
        span.textContent = String(entry.reading);
        popup.appendChild(span);
      }}
      const defsDiv = document.createElement('div');
      defsDiv.className = 'defs';
      (entry.definitions || []).forEach((d, i) => {{
        if (i) defsDiv.appendChild(document.createElement('br'));
        defsDiv.appendChild(document.createTextNode('\u2022 ' + String(d)));
      }});
      popup.appendChild(defsDiv);
    }}
    popup.style.display = 'block';
  }}

  // Follow-along: highlight the line matching the current playback position
  // and keep it in view. Entirely client-side: the iframe's host page can't
  // observe the <audio> element's position, so this lives in the page itself.
  (function setupFollow() {{
    const player = document.getElementById('player');
    if (!player) return;
    const rows = Array.from(document.querySelectorAll('.line-row[data-start]'));
    const chk = document.getElementById('followchk');
    const status = document.getElementById('followstatus');
    let current = null;

    player.addEventListener('timeupdate', function () {{
      const t = player.currentTime + PAGE_START_OFFSET;
      const row = rows.find(r => t >= parseFloat(r.dataset.start) && t <= parseFloat(r.dataset.end));
      if (status) {{
        const i = row ? rows.indexOf(row) + 1 : 0;
        status.textContent = i ? `line ${{i}} of ${{rows.length}}` : '';
      }}
      if (!row || row === current) return;
      if (current) current.classList.remove('playing');
      row.classList.add('playing');
      current = row;
      if (chk && chk.checked) {{
        row.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
      }}
    }});
  }})();

  function seekTo(seconds) {{
    const player = document.getElementById('player');
    if (!player) return;
    player.currentTime = Math.max(0, seconds - PAGE_START_OFFSET);
    player.play();
  }}
</script>
</body>
</html>
"""
