"""
cli_subtitle.py -- `python cli.py import-subtitle`: the headless twin of the
Source stage's "Import subtitle file", through the same
services/subtitle_import_service.py (so the checks, the history snapshot and
the confirmations behave the same). Kept out of cli.py, which has no room to grow.

  python cli.py import-subtitle --id 12 --file lines.srt
  python cli.py import-subtitle --id 12 --file lines.en.srt --as translation --yes
  python cli.py import-subtitle --find track.mp3 *.srt *.vtt     # rank sidecar names, import nothing
"""

import db
import subtitle_parse
import subtitle_sidecar
import translate_engines
from services import subtitle_import_service as svc
from lib.errors import ServiceError


def _problems(report: dict) -> None:
    for p in report["problems"]:
        print(f"  [{p['severity']}] {p['message']}")


def _find(args) -> None:
    names = [n.replace("\\", "/").rsplit("/", 1)[-1] for n in args.find[1:]]
    drama = db.get_drama(args.id) if args.id is not None else None
    ranked = subtitle_sidecar.rank_sidecars(args.find[0], names, (drama or {}).get("source_language"))
    for c in ranked:
        print(f"{c.name}\t{c.format}\t{c.language or c.language_token or '-'}")
    if len(ranked) > 1:
        print(f"{len(ranked)} candidates: pass the one you want with --file.")
    elif not ranked:
        print("No subtitle file matches that media name.")


def cmd_import_subtitle(args) -> None:
    if args.find:
        return _find(args)
    if args.id is None or not args.file:
        raise SystemExit("import-subtitle: needs --id and --file (or --find MEDIA NAMES...).")
    try:
        with open(args.file, "rb") as f:
            data = f.read(subtitle_parse.MAX_FILE_BYTES + 1)
    except OSError as e:
        raise SystemExit(f"import-subtitle: couldn't read the file: "
                         f"{translate_engines.redact_secrets(str(e))}")
    options = dict(encoding=args.encoding, mode=args.mode, split_bilingual=args.split_bilingual,
                   translation_first=args.translation_first)
    try:
        report = svc.preview_import(args.id, data, args.file, **options)
        print(f"{report['format'].upper()}, {report['cue_count']} cue(s), read as "
              f"{report['encoding']}{' (guessed)' if report['encoding_guessed'] else ''}.")
        _problems(report)
        if report["blocking"] or report["blocked_reason"]:
            raise SystemExit(f"import-subtitle: {report['blocked_reason'] or 'nothing was imported.'}")
        result = svc.import_subtitle(args.id, data, args.file, confirm_replace_lines=args.yes,
                                     confirm_overwrite=args.yes, **options)
    except ServiceError as e:
        hint = " Pass --yes to confirm." if (e.details or {}).get("reason", "").startswith("confirm_") else ""
        raise SystemExit(f"import-subtitle: {translate_engines.redact_secrets(e.message)}{hint}")
    detail = "" if result["mode"] == "source" else f", {result['unmatched_cues']} cue(s) matched no line"
    print(f"Wrote {result['lines_written']} line(s) as {result['mode']}{detail}. "
          "To re-align them with the audio, use Review > Re-time.")


def register(sub) -> None:
    p = sub.add_parser("import-subtitle", help="Import an SRT/VTT/ASS/LRC file into a title "
                       "as source lines or as translation (the app's Import subtitle file)")
    p.add_argument("--id", type=int, default=None)
    p.add_argument("--file", default=None, metavar="FILE", help="The subtitle file.")
    p.add_argument("--as", dest="mode", default="source", choices=list(svc.MODES),
                   help="source (default): the cues become the lines. translation: the text goes "
                        "on existing lines by time overlap.")
    p.add_argument("--encoding", default=None, help="Force a text encoding when the guess is wrong.")
    p.add_argument("--split-bilingual", action="store_true",
                   help="Two lines per cue: first line source, second translation.")
    p.add_argument("--translation-first", action="store_true",
                   help="With --split-bilingual: the first line is the translation.")
    p.add_argument("--yes", action="store_true",
                   help="Confirm replacing existing lines or overwriting existing translations "
                        "(the old state is saved to history first).")
    p.add_argument("--find", nargs="+", metavar=("MEDIA", "NAME"), default=None,
                   help="Rank file NAMEs as sidecars of MEDIA and print them; imports nothing. "
                        "With --id, the title's source language ranks first.")
    p.set_defaults(func=cmd_import_subtitle)
