"""
cli_translate.py -- `python cli.py translate`: translate one title or a batch
from the command line with the same engine, glossary, style guide and locale
the app uses. Kept out of cli.py, which has no room to grow.

  python cli.py translate --status aligned --engine claude --api-key $ANTHROPIC_API_KEY
  python cli.py translate --id 12 --glossary-affected --term 沈清疑
"""

import contextlib
import os

import background_jobs
import bulk_translate
import db
import translate_engines
import translation_guide as tguide
from cli import _gemini_free_tier, _gpu_lock, _ollama_url, _run_batch
from core import lines_from_rows
from lib.errors import ServiceError
from services import (engine_routing_service, glossary_retranslate_service, line_provenance_service,
                      settings_service, translate_run_service, translate_service,
                      workspace_job_service)
from services.translate_run_service import (engine_cap_applies, get_translate_config_defaults,
                                            validate_run_options)


def _monthly_cap_setting():
    """The saved monthly cap (Settings, then .env), same source as the service."""
    return settings_service.get_monthly_cap_usd() or None


def _load_novel_reference(drama):
    ddir = db.drama_dir(drama["id"])
    if drama.get("novel_reference_filename"):
        p = os.path.join(ddir, drama["novel_reference_filename"])
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return f.read()
    return None


# The engine a bare --api-key (no --engine) is assumed to belong to: the
# CLI's old --engine default.
_API_KEY_DEFAULT_ENGINE = "claude"


def _flag_or(args, name, defaults):
    """An explicit CLI flag wins; unset (None/missing) uses the per-drama default."""
    value = getattr(args, name, None)
    return defaults[name] if value is None else value


def _parse_fallback_arg(value, reflect=False) -> list:
    """--fallback "<engine>[,<engine>]" as a list of engine names, checked
    the way the translate run API checks fallback_chain (at most
    translate_engines.MAX_FALLBACK_ENGINES, known engines, normal runs
    only); the chain rules against the primary engine are checked per
    drama (translate_engines.fallback_chain_error)."""
    names = [n.strip() for n in (value or "").split(",") if n.strip()]
    if not names:
        return []
    if len(names) > translate_engines.MAX_FALLBACK_ENGINES:
        raise SystemExit(f"translate: --fallback takes at most "
                         f"{translate_engines.MAX_FALLBACK_ENGINES} engines.")
    if reflect:
        raise SystemExit("translate: --fallback only applies to a normal translation run, "
                         "not --reflect.")
    for n in names:
        if n not in translate_engines.ENGINES:
            raise SystemExit(f"translate: --fallback: {translate_engines.unknown_engine_message(n)}")
    return names


def _resolve_glossary_terms(drama: dict, refs) -> list:
    """--term values (a term id or the exact source text) -> term ids of the
    drama's series glossary. SystemExit if a value matches no term or several."""
    series_id = drama.get("series_id")
    terms = db.list_glossary_terms(series_id) if series_id else []
    ids = []
    for ref in refs:
        ref = str(ref).strip()
        hits = [t for t in terms
                if (ref.isdigit() and t["id"] == int(ref)) or (t.get("term_original") or "") == ref]
        if not hits:
            raise SystemExit(f"--term {ref!r} matches no term in this drama's glossary.")
        if len(hits) > 1:
            raise SystemExit(f"--term {ref!r} matches {len(hits)} glossary terms "
                             f"(ids {', '.join(str(t['id']) for t in hits)}); use the id.")
        if hits[0]["id"] not in ids:
            ids.append(hits[0]["id"])
    return ids


def cmd_translate(args):
    if args.engine and args.engine not in translate_engines.ENGINES:
        raise SystemExit(f"translate: {translate_engines.unknown_engine_message(args.engine)}")
    fallback_names = _parse_fallback_arg(getattr(args, "fallback", None),
                                         reflect=getattr(args, "reflect", False))
    glossary_affected = getattr(args, "glossary_affected", False)
    if glossary_affected and not args.id:
        raise SystemExit("--glossary-affected needs --id (one drama at a time, as in the app).")
    if getattr(args, "include_hand_edited", False) and not glossary_affected:
        raise SystemExit("--include-hand-edited only applies with --glossary-affected.")
    if getattr(args, "term", None) and not glossary_affected:
        raise SystemExit("--term only applies with --glossary-affected.")
    query_status = args.status or "aligned"
    dramas = [db.get_drama(args.id)] if args.id else db.list_dramas(status=query_status)
    # Same default as the service: an explicit --engine, else the drama's
    # saved translation_engine, else the Settings engine for everyday
    # translation (capability "translation.cheap").
    def _engine_name_for(d):
        return (args.engine or d.get("translation_engine")
                or engine_routing_service.resolve_capability("translation.cheap"))
    _engines = {}

    def _own_flags(name):
        # --api-key/--model belong to --engine when it's given, else to the
        # old default engine (claude). A drama saved with another engine
        # uses that engine's own configured key -- never someone else's.
        return (name == args.engine) if args.engine else name == _API_KEY_DEFAULT_ENGINE

    def _engine_for(name):
        own_flags = _own_flags(name)
        if name not in _engines:
            _engines[name] = translate_engines.get_engine(
                name,
                (args.api_key if own_flags and args.api_key
                 else translate_service.resolve_api_key(name)),
                args.model if own_flags else None,
                free_tier=_gemini_free_tier(name),
                base_url=_ollama_url(args) if name == "ollama" else None)
        return _engines[name]
    # UI parity -- Workspace's own Translate button builds this
    # same optional summary_engine before starting the job (defaulting to
    # local Ollama); a missing/unreachable one just skips the summary
    # rather than failing the translate command.
    summary_engine_choice = (getattr(args, "episode_summary_engine", None)
                             or settings_service.get_preference("episode_summary_engine"))
    summary_key = getattr(args, "episode_summary_api_key", None) or (
        None if summary_engine_choice == "ollama"
        else translate_service.resolve_api_key(summary_engine_choice))
    try:
        if summary_engine_choice != "ollama" and not summary_key:
            raise ValueError("no key for the episode-summary engine")
        summary_engine = translate_engines.get_engine(
            summary_engine_choice, summary_key,
            free_tier=_gemini_free_tier(summary_engine_choice),
            base_url=_ollama_url(args) if summary_engine_choice == "ollama" else None)
    except Exception:
        summary_engine = None
    # A paid summary engine counts against the monthly cap too (checked
    # right before its call, in finish_translation_run).
    summary_monthly_cap = getattr(args, "monthly_cap", None)
    if summary_monthly_cap is None:
        summary_monthly_cap = _monthly_cap_setting()

    def step(d):
        rows = db.load_lines(d["id"])
        if not rows:
            print(f"#{d['id']} skipped: no aligned lines yet.")
            return
        lines = lines_from_rows(rows)
        engine_name = _engine_name_for(d)
        if args.api_key and not args.engine and engine_name != _API_KEY_DEFAULT_ENGINE:
            print(f"#{d['id']} skipped: saved engine {engine_name}; pass --engine {engine_name} "
                  f"and its key, or omit --api-key to use the saved keys.")
            return
        chain_names = [engine_name] + fallback_names
        chain_error = translate_engines.fallback_chain_error(chain_names)
        if chain_error:
            print(f"#{d['id']} skipped: {chain_error}")
            return
        missing = [n for n in fallback_names if not translate_service.resolve_api_key(n)]
        if missing:
            print(f"#{d['id']} skipped: no {missing[0]} key is configured for --fallback.")
            return
        # Same defaults the service/React use.
        tdefaults = get_translate_config_defaults(d.get("content_mode") == "novel_narration")
        style_preset = args.style_preset or (
            "novel" if d.get("content_mode") == "novel_narration" else "audio_drama")
        # The same option checks as start_translate_run. An invalid flag is
        # the same for every drama, so it stops the batch instead of failing
        # each title in turn.
        try:
            validate_run_options(
                engine_name, args.model if _own_flags(engine_name) else None,
                locale=args.locale or settings_service.get_preference("default_locale"),
                style_preset=style_preset,
                context_window=_flag_or(args, "context_window", tdefaults),
                context_window_ahead=_flag_or(args, "context_window_ahead", tdefaults),
                batch_size=_flag_or(args, "batch_size", tdefaults),
                job_cost_cap_usd=getattr(args, "cost_cap", None),
                gemini_free_tier=_gemini_free_tier(engine_name))
        except ServiceError as e:
            raise SystemExit(f"translate: {e.message}")
        if background_jobs.is_running(f"translate_{d['id']}"):
            print(f"#{d['id']} skipped: a translation is already running for this drama "
                  f"in the app.")
            return
        engine = _engine_for(engine_name)
        novel_reference = _load_novel_reference(d)
        # UI parity: without these, a CLI-run translation skipped the
        # series glossary, craft/style guidelines, and locale entirely --
        # a real, confirmed gap between what the Workspace Translate
        # button sends and what this command sent for the same drama.
        # The pronoun-default/genre-notes toggles come from the flags when given
        # (and are then saved for the title), else from the title's saved
        # choice, as in the app. Everything else --
        # series glossary, learned style profile, emotion guidance, gender
        # hints, speaker names -- comes from the same builder the translate
        # run service uses.
        no_genre = getattr(args, "no_genre_notes", None)
        include_genre_notes = None if no_genre is None else not no_genre
        default_female_pronouns = getattr(args, "female_pronouns", None)
        glossary_terms, style_guidelines, character_names = \
            workspace_job_service.build_run_style_context(
                d["id"], d, lines, style_preset,
                include_genre_notes=include_genre_notes,
                default_female_pronouns=default_female_pronouns)
        target_ids = None
        if glossary_affected:
            # Same selection as the app's "Re-translate lines affected by the
            # glossary": lines whose English isn't known to be machine-made
            # are left alone unless --include-hand-edited.
            term_ids = (_resolve_glossary_terms(d, args.term)
                        if getattr(args, "term", None) else None)
            target_ids = set(glossary_retranslate_service.affected_line_ids(
                d["id"], include_hand_edited=getattr(args, "include_hand_edited", False),
                term_ids=term_ids))
            print(f"#{d['id']} glossary terms: "
                  + (", ".join(str(i) for i in sorted(term_ids)) if term_ids is not None
                     else "all")
                  + f"; {len(target_ids)} lines selected.")
            if not target_ids:
                print(f"#{d['id']} skipped: no machine-translated lines are affected by the "
                      f"glossary (hand-edited lines need --include-hand-edited).")
                return
        force = args.force or target_ids is not None
        print(f"#{d['id']} translating "
              f"{len(target_ids) if target_ids is not None else len(lines)} lines with {engine_name}"
              + (" (+ novel reference)" if novel_reference else "") + "...")
        _id_by_idx = {ln.idx: ln.id for ln in lines if getattr(ln, "id", None) is not None}
        # Same caps as the Workspace Translate job: per job (--cost-cap)
        # and per calendar month (--monthly-cap, or BAIHE_MONTHLY_CAP_USD).
        # Like the service, the monthly cap only covers paid engines
        # (_cap_applies: not local/free engines, not Gemini's free tier).
        # With --fallback, each engine in the chain gets its own cap
        # (FallbackEngine enforces it), as the translate run API does.
        monthly_setting = getattr(args, "monthly_cap", None)
        if monthly_setting is None:
            monthly_setting = _monthly_cap_setting()
        month_spend = db.get_month_spend() if monthly_setting else 0.0
        caps = []
        for name in chain_names:
            # Neither cap applies to a free or local engine, as in the service.
            if not engine_cap_applies(name, _gemini_free_tier(name)):
                caps.append(None)
                continue
            cap, refusal = translate_engines.resolve_cost_cap(
                getattr(args, "cost_cap", None), monthly_setting,
                month_spend if monthly_setting else 0.0)
            if refusal:
                raise RuntimeError(refusal)
            caps.append(cap)
        translate_run_service.save_style_toggles(d["id"], include_genre_notes, default_female_pronouns, getattr(args, "thinking", None))
        if fallback_names:
            engine = translate_engines.FallbackEngine(
                [engine] + [_engine_for(n) for n in fallback_names], chain_names, caps,
                failed_usage_cb=lambda choice, eng, inp, out, cache_read=0, cache_write=0,
                did=d["id"]: db.log_usage(
                    did, choice, getattr(eng, "model", choice), "translate", inp, out,
                    translate_engines.estimate_cost_for_engine(eng, inp, out, cache_read,
                                                               cache_write),
                    cache_read_tokens=cache_read))
            cost_cap = None
        else:
            cost_cap = caps[0]
        cap_reached = {}
        def _progress(frac, did=d["id"]):
            if _gpu_holder:
                # --engine ollama holds the cross-process GPU
                # lock for this whole batch (see below) -- refreshed here,
                # on every batch's own progress tick, so a long run doesn't
                # look abandoned to another process before it's done.
                db.heartbeat_gpu_lock(_gpu_holder)
            print(f"  #{did}: {frac*100:.0f}%", end="\r")

        style_note = (args.style_note if args.style_note is not None
                      else settings_service.get_preference("default_style_note"))
        scene_aware = settings_service.get_preference("scene_aware_batches")
        # Same settings the Workspace job records with each line.
        provenance = line_provenance_service.translate_run_tracker(
            d["id"], lines, engine, engine_name, glossary_terms,
            locale=args.locale or settings_service.get_preference("default_locale"),
            style_preset=style_preset, reflect=bool(getattr(args, "reflect", False)),
            context_window=_flag_or(args, "context_window", tdefaults),
            context_window_ahead=_flag_or(args, "context_window_ahead", tdefaults),
            batch_size=_flag_or(args, "batch_size", tdefaults),
            style_note=style_note or "", style_guidelines=style_guidelines or "",
            scene_aware_batches=scene_aware)
        if force and any(ln.en for ln in lines):
            # Same data-loss guard as translate_run_service: keep the old
            # translation restorable from history before it's overwritten.
            db.save_line_history_snapshot(d["id"], lines, "before force re-translate")
        # Same as the Workspace Translate job: writes `en` only, and
        # records each translated line's provenance.
        if target_ids is not None:
            save_cb, notes_cb = bulk_translate.own_lines_callbacks(d["id"], lines, provenance)
        else:
            def save_cb(ls, did=d["id"]):
                db.save_lines(did, ls, fields=("en",))
                provenance(ls)

            def notes_cb(notes, did=d["id"]):
                db.save_translation_notes(did, notes, id_by_idx=_id_by_idx)
        _, batch_errors = translate_engines.translate_lines_with_engine(
            lines, engine, drama_meta=d,
            style_note=style_note,
            novel_reference=novel_reference, force_retranslate=force, target_ids=target_ids,
            locale=args.locale or settings_service.get_preference("default_locale"),
            glossary_terms=glossary_terms,
            style_guidelines=style_guidelines, character_names=character_names,
            ollama_num_ctx_override=(args.ollama_num_ctx if args.ollama_num_ctx is not None
                                     else settings_service.get_ollama_num_ctx_override() or None),
            context_window=_flag_or(args, "context_window", tdefaults),
            context_window_ahead=_flag_or(args, "context_window_ahead", tdefaults),
            batch_size=_flag_or(args, "batch_size", tdefaults),
            reflect=getattr(args, "reflect", False), scene_aware_batches=scene_aware,
            notes_cb=notes_cb,
            progress_cb=_progress,
            save_cb=save_cb,
            usage_cb=lambda inp, out, cache_read=0, cache_write=0, did=d["id"]: db.log_usage(
                did, (engine.active_choice if isinstance(engine, translate_engines.FallbackEngine)
                      else engine_name),
                getattr(engine, "model", engine_name), "translate", inp, out,
                translate_engines.estimate_cost_for_engine(engine, inp, out, cache_read, cache_write),
                cache_read_tokens=cache_read),
            cost_cap_usd=cost_cap,
            cap_cb=lambda spent: cap_reached.update(spent=spent),
        )
        # Same post-translate steps as the Workspace Translate job:
        # enforce_exact glossary terms, density flags, a saved version,
        # persisted batch errors -- and "translated" only once no line is
        # left, so the retry suggested below (default --status aligned)
        # still finds this drama.
        recheck = set()
        bulk_translate.finish_translation_run(
            d["id"], lines, engine, engine_name, style_preset, glossary_terms, batch_errors,
            summary_engine=summary_engine, summary_engine_choice=summary_engine_choice,
            summary_monthly_cap_usd=summary_monthly_cap,
            line_scoped=target_ids is not None, enforce_ids=target_ids,
            flags_needing_recheck=recheck)
        if recheck:
            print(f"\n#{d['id']} {len(recheck)} line(s) changed while the job ran, so their "
                  f"review flags weren't saved; recheck line id(s) "
                  f"{', '.join(map(str, sorted(recheck)))}.")
        for ev in getattr(engine, "events", None) or []:
            print(f"\n#{d['id']} switched from {ev['from']} to {ev['to']} ({ev['reason']}).")
        if "spent" in cap_reached:
            print(f"\n#{d['id']} stopped at the spending cap after about ${cap_reached['spent']:.2f} "
                  f"-- finished lines were kept; re-run with a higher cap to continue.")
        elif batch_errors:
            print(f"\n#{d['id']} translated with {len(batch_errors)} batch failure(s) after "
                  f"backoff retries -- re-run this command to retry just the missing lines.")
        else:
            print(f"\n#{d['id']} translated.")

    # Only --engine ollama actually touches the GPU here (every
    # other translate engine is a remote API call) -- the cross-process
    # lock only needs to guard that case, not every translate run.
    _gpu_ctx = (_gpu_lock(f"CLI translate --engine ollama ({len(dramas)} drama(s))")
               if any("ollama" in (_engine_name_for(d), *fallback_names) for d in dramas if d)
               else contextlib.nullcontext(None))
    with _gpu_ctx as _gpu_holder:
        _run_batch(dramas, step, "translate")


def add_translate_parser(sub):
    p_translate = translate_engines.think_flag(sub.add_parser("translate"))
    p_translate.add_argument("--id", type=int)
    p_translate.add_argument("--status")
    # No argparse choices: a removed engine name gets the same plain refusal
    # as the API instead of a generic "invalid choice" error.
    p_translate.add_argument("--engine", default=None,
                             help=f"Translate engine ({', '.join(translate_engines.ENGINES)}).")
    p_translate.add_argument("--api-key", default=None,
                             help="Key for --engine; omit to use the saved key.")
    p_translate.add_argument("--model", default=None)
    p_translate.add_argument("--episode-summary-engine", default=None,
                             choices=list(translate_engines.ENGINES),
                             help="Engine for the once-per-episode running-summary call "
                                  "made after a drama finishes translating, fed forward as "
                                  "continuity context into the next episode of the same series. "
                                  "Defaults to the Settings episode-summary engine (local Ollama "
                                  "until changed; a fixed once-per-episode cost); if "
                                  "it's unreachable, or a cloud engine is picked with no key, the "
                                  "summary is skipped rather than failing the translate run.")
    p_translate.add_argument("--episode-summary-api-key", default=None,
                             help="API key for --episode-summary-engine, if it isn't ollama.")
    p_translate.add_argument("--style-note", default=None)
    p_translate.add_argument("--style-preset", default=None,
                              choices=list(tguide.STYLE_PRESETS),
                              help="Matches the Workspace tab's own style-guidance preset -- "
                                   "affects phrasing/pacing guidance, not language or content. "
                                   "Defaults to the same per-content-mode preset Workspace picks "
                                   "(\"novel\" for a novel-narration drama, \"audio_drama\" "
                                   "otherwise) unless set explicitly.")
    p_translate.add_argument("--locale", default=None, choices=list(settings_service.LOCALE_CHOICES),
                             help="Default: the Settings English variant (en-US until changed).")
    p_translate.add_argument("--female-pronouns", action="store_true", default=None,
                           help="Default ambiguous pronouns to she/her and save that choice "
                                "for the title. Without --female-pronouns or "
                                "--no-female-pronouns the title's saved choice applies (off "
                                "until chosen).")
    p_translate.add_argument("--no-female-pronouns", action="store_false", dest="female_pronouns",
                           default=None, help="Turn the she/her default off and save that.")
    p_translate.add_argument("--no-genre-notes", action="store_true", default=None,
                           help="Leave out the baihe/GL genre guidance and save that choice "
                                "for the title (on until chosen otherwise).")
    p_translate.add_argument("--genre-notes", action="store_false", dest="no_genre_notes",
                           default=None, help="Include the genre guidance and save that.")
    p_translate.add_argument("--force", action="store_true",
                              help="Re-translate everything, including lines that already have a translation")
    p_translate.add_argument("--glossary-affected", action="store_true",
                             help="Re-translate only the lines the glossary affects (a term or "
                                  "alias in the source, or a banned translation in the English). "
                                  "Needs --id. Hand-edited lines are left alone.")
    p_translate.add_argument("--include-hand-edited", action="store_true",
                             help="With --glossary-affected: also replace hand-edited lines "
                                  "(a snapshot is saved first).")
    p_translate.add_argument("--term", action="append", default=None, metavar="ID_OR_TEXT",
                             help="With --glossary-affected: only the lines these glossary terms "
                                  "affect (a term id, or its exact source text; repeat for "
                                  "several). Default: every term.")
    p_translate.add_argument("--ollama-num-ctx", type=int, default=None,
                              help="Override Ollama's context window size. Only ever raises it "
                                   "above the automatic per-prompt estimate, never below -- "
                                   "leave unset to size it automatically (recommended).")
    p_translate.add_argument("--ollama-url", default=None,
                             help="Base URL for a non-default Ollama server (e.g. remote/Docker).")
    p_translate.add_argument("--reflect", action="store_true",
                             help="'High quality' Reflect mode: three passes per batch "
                                  "(faithful draft, critique, rewrite) instead of one -- costs "
                                  "about 3x as much. The critique is saved as a translation note "
                                  "per line.")
    p_translate.add_argument("--cost-cap", type=float, default=None,
                           help="Stop a drama's translation once its estimated spend reaches this "
                                "many USD (finished lines are kept).")
    p_translate.add_argument("--monthly-cap", type=float, default=None,
                           help="Refuse to start / stop once this calendar month's logged spend "
                                "reaches this many USD. Defaults to the saved Settings/.env monthly cap.")
    # Matches the Workspace tab's own three sliders. Unset means
    # the service's per-drama defaults (translate_run_service.
    # get_translate_config_defaults): 10/6/30.
    p_translate.add_argument("--context-window", type=int, default=None,
                           help="Lines of already-translated context shown from before each "
                                "batch (default 10). 0 turns this off.")
    p_translate.add_argument("--context-window-ahead", type=int, default=None,
                           help="Lines of source text shown from after each batch, to resolve "
                                "a reference that's only disambiguated later (default 6). 0 "
                                "turns this off.")
    p_translate.add_argument("--batch-size", type=int, default=None,
                           help="Lines translated per request (default 30). More lines per "
                                "request is cheaper/faster overall but a bigger single point "
                                "of failure.")
    p_translate.add_argument("--fallback", default=None, metavar="ENGINE[,ENGINE]",
                             help="Up to 2 engines tried in order if the main engine keeps "
                                  "failing (rate limit, timeout, connection, bad key) after "
                                  "its retries -- same kind as the main engine (AI with AI, "
                                  "translation-only with translation-only); not with --reflect. "
                                  "Same rules as the Translate stage's fallback engines.")
    p_translate.set_defaults(func=cmd_translate)
