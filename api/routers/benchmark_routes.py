"""
api/routers/benchmark_routes.py -- the Benchmark Lab (golden sets,
persistent per-run results, Model Arena). Thin: see
services/benchmark_lab_service.py.

- Reads (`admin.diagnostics`, like the rest of Diagnostics): options, cases,
  sets, runs, one run's results, the arena view, and the pre-run estimate
  (a POST only because it takes the selection as a body; it spends nothing).
- Writes are `local_only()`: adding, importing and deleting cases, "add as
  regression test" from a line, and starting a run -- a run can spend on the
  owner's paid keys, so it is PC-only and needs `confirm=true` (the page
  shows the estimate first). The run is refused when the monthly cap is used
  up or the estimate is over what is left of it, and stops at the cap.
- "Build a set from a reviewed title" reads a title's lines and writes cases, so
  it is PC only too (the title's ownership is still checked).
- The owner's clip set is uploaded here (`POST /clips/import`, multipart: audio files
  plus typed rows or a manifest); `POST /clips/check` only reports problems and
  language warnings and saves nothing. A case can be excluded from scoring, or have its
  reference edited in place (every change is logged and versions the reference).
- A run can be deleted (confirm=true) and a run or a comparison exported as CSV or JSON.
  A comparison scores only the cases every run scored on the same reference version.
- The settings sweep tunes transcription on one set and language: its estimate is a read, starting or
  resuming it and approving or rejecting its winner are PC only and need `confirm=true`. The
  translation sweep (`/translate-sweep`) works the same way for one engine.
- A translation run may ask for an LLM judge (`judge` on the run request and the
  estimate); the judge's summary and per-case scores ride on the run reads.
"""

import json
import os
from typing import List, Optional

from fastapi import APIRouter, Path, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from starlette.datastructures import UploadFile

from api.auth import local_only, require_engines_allowed, require_permission
from api.benchmark_schemas import (BenchmarkArena, BenchmarkCase, BenchmarkCaseCreate,
                                   BenchmarkCaseDelete, BenchmarkCaseList, BenchmarkDeleted,
                                   BenchmarkEstimate, BenchmarkImportRequest,
                                   BenchmarkImportResult, BenchmarkOptions,
                                   BenchmarkRegressionResult, BenchmarkRunDetail,
                                   BenchmarkRunList, BenchmarkRunRequest,
                                   BenchmarkRunStarted, BenchmarkSetList)
from api.routers.settings_routes import require_confirm
from api.schemas import (BenchmarkCaseEdits, BenchmarkCaseExcluded, BenchmarkCaseReference,
                         BenchmarkCorrectionsAddRequest, BenchmarkCorrectionsAddResult,
                         BenchmarkHardSubAddRequest, BenchmarkHardSubAddResult,
                         BenchmarkHardSubReadEstimate, BenchmarkHardSubReadRequest,
                         BenchmarkHardSubReadStarted,
                         BenchmarkHardSubReadResult,
                         BenchmarkTitleCorrections,
                         BenchmarkClipCheck, BenchmarkClipCheckRequest, BenchmarkClipImportResult,
                         BenchmarkPairNext, BenchmarkPickRequest, BenchmarkPickSummary,
                         BenchmarkRunDelete, BenchmarkSetBuildRequest, BenchmarkSetBuildResult,
                         BenchmarkSweepDecisionRequest, BenchmarkSweepDecisions, BenchmarkSweepEstimate,
                         BenchmarkSweepRequest, BenchmarkSweepStarted, BenchmarkSweepState,
                         BenchmarkTranslateSweepEstimate, BenchmarkTranslateSweepRequest,
                         BenchmarkTranslateSweepStarted, BenchmarkTranslateSweepState,
                         error_responses)
from lib.capped_request import read_capped_form
from lib.errors import InvalidInputError
from services import benchmark_clip_service as clip_svc
from services import benchmark_export_service as export_svc
from services import benchmark_hardsub_service as hardsub_svc
from services import hardsub_speaker_read_service as hardsub_read_svc
from services import benchmark_judge_service as judge_svc
from services import benchmark_lab_service as svc
from services import benchmark_picks_service as picks_svc
from services import benchmark_reference_service as reference_svc
from services import benchmark_set_builder_service as builder_svc
from services import benchmark_sweep_service as sweep_svc
from services import benchmark_translate_sweep_service as tsweep_svc

router = APIRouter(prefix="/api/benchmark", tags=["benchmark"])

_ERRS = error_responses(404, 409, 422)
_READ = [require_permission("admin.diagnostics")]


def _configs(body: BenchmarkRunRequest) -> list:
    return [c.model_dump(exclude_none=True) for c in body.configs]


def _judge(body: BenchmarkRunRequest):
    """(judge config without the flag, allow_same_model) from the request."""
    if body.judge is None:
        return None, False
    cfg = body.judge.model_dump()
    return {"engine": cfg["engine"], "model": cfg["model"]}, cfg["allow_same_model"]


def _with_judge(runs: list, rows: list = None) -> None:
    """Adds the judge summary and the owner's picks to each run and, in Arena
    rows, the scores and picks to each cell (cell i belongs to run i)."""
    judge_svc.annotate_runs(runs)
    picks_svc.annotate_runs(runs)
    for i, run in enumerate(runs):
        cells = [row["results"][i] for row in rows or [] if row["results"][i] is not None]
        if cells:
            judge_svc.annotate_results(run, cells)
            picks_svc.annotate_results(run, cells)


@router.get("/options", dependencies=_READ, response_model=BenchmarkOptions,
            summary="Stages, tiers and the engines/models a run can use (no keys)")
def get_options():
    return svc.get_options()


@router.get("/cases", dependencies=_READ, response_model=BenchmarkCaseList, responses=_ERRS,
            summary="Benchmark cases, optionally filtered by stage, tier and set")
def get_cases(stage: Optional[str] = Query(default=None, max_length=20),
              tier: Optional[str] = Query(default=None, max_length=20),
              set_name: Optional[str] = Query(default=None, max_length=60)):
    return svc.list_cases(stage, tier, set_name)


@router.get("/sets", dependencies=_READ, response_model=BenchmarkSetList,
            summary="Golden sets: one row per stage, tier and set name with case counts")
def get_sets():
    return svc.list_sets()


@router.post("/cases", dependencies=[local_only()], response_model=BenchmarkCase,
             responses=_ERRS, summary="PC only: add a translation case by hand")
def post_case(body: BenchmarkCaseCreate):
    return svc.create_case(body.label, body.source_text, body.reference_text,
                           body.source_language, body.tier, body.set_name)


@router.post("/cases/{case_id}/delete", dependencies=[local_only()],
             response_model=BenchmarkDeleted, responses=_ERRS,
             summary="PC only: delete a benchmark case (confirm=true)")
def delete_case(body: BenchmarkCaseDelete, case_id: int = Path(ge=1)):
    require_confirm(body.confirm)
    return svc.delete_case(case_id)


@router.post("/import", dependencies=[local_only()], response_model=BenchmarkImportResult,
             responses=_ERRS,
             summary="PC only: import a golden set from pasted JSONL or TSV (nothing is downloaded)")
def post_import(body: BenchmarkImportRequest):
    return svc.import_golden_set(body.set_name, body.text, body.format, body.tier,
                                 body.source_language)


@router.post("/dramas/{drama_id}/lines/{line_id}/regression", dependencies=[local_only()],
             response_model=BenchmarkRegressionResult, responses=_ERRS,
             summary="PC only: keep a hand-fixed line as a regression test")
def post_regression(drama_id: int = Path(ge=1), line_id: int = Path(ge=1)):
    return svc.add_regression_case(drama_id, line_id)


@router.post("/sets/from-title", dependencies=[local_only()], response_model=BenchmarkSetBuildResult,
             responses=_ERRS,
             summary="PC only: build a set of cases from a title's reviewed lines (dry_run counts only)")
def post_build_set(body: BenchmarkSetBuildRequest, request: Request):
    return builder_svc.build_set(
        request.state.principal, body.drama_id, body.set_name, body.include, body.line_start,
        body.line_end, body.scene_count, body.lines_per_case, body.dry_run)


@router.get("/dramas/{drama_id}/corrections", dependencies=_READ,
            response_model=BenchmarkTitleCorrections, responses=_ERRS,
            summary="How many of a title's lines were corrected and the raw-to-edited error rate")
def get_corrections(request: Request, drama_id: int = Path(ge=1)):
    return reference_svc.title_stats(request.state.principal, drama_id)


@router.post("/dramas/{drama_id}/corrections/add", dependencies=[local_only()],
             response_model=BenchmarkCorrectionsAddResult, responses=_ERRS,
             summary="PC only: add a title's corrected lines, with their audio, to a test set (dry_run counts only)")
def post_add_corrections(body: BenchmarkCorrectionsAddRequest, request: Request,
                         drama_id: int = Path(ge=1)):
    return reference_svc.add_title_lines(request.state.principal, drama_id, body.set_name, body.dry_run, body.include_all)


@router.post("/dramas/{drama_id}/hard-sub-cases", dependencies=[local_only()],
             response_model=BenchmarkHardSubAddResult, responses=_ERRS,
             summary="PC only: add hard-sub test cases (pictures of the subtitle band) for chosen lines of a title")
def post_add_hard_sub_cases(body: BenchmarkHardSubAddRequest, request: Request, drama_id: int = Path(ge=1)):
    return hardsub_svc.add_lines(request.state.principal, drama_id, body.line_ids, body.set_name,
                                 body.band_top, body.band_bottom)


@router.post("/dramas/{drama_id}/hard-sub-reading/estimate", dependencies=[local_only()],
             response_model=BenchmarkHardSubReadEstimate, responses=_ERRS,
             summary="PC only: the most reading a title's hard subs by speaker can cost")
def post_hard_sub_reading_estimate(body: BenchmarkHardSubReadRequest, request: Request,
                                   drama_id: int = Path(ge=1)):
    return hardsub_read_svc.estimate(request.state.principal, drama_id, body.engine, body.model,
                                     body.speaker_groups, body.band_top, body.band_bottom)


@router.post("/dramas/{drama_id}/hard-sub-reading", dependencies=[local_only()],
             response_model=BenchmarkHardSubReadStarted, responses=_ERRS,
             summary="PC only: read a title's hard subs by speaker with a vision model (confirm=true; result on the job)")
def post_hard_sub_reading(body: BenchmarkHardSubReadRequest, request: Request, drama_id: int = Path(ge=1)):
    require_confirm(body.confirm)
    return hardsub_read_svc.start(request.state.principal, drama_id, body.engine, body.model,
                                  body.speaker_groups, body.band_top, body.band_bottom, body.max_cost_usd)


@router.get("/dramas/{drama_id}/hard-sub-reading", dependencies=[local_only()],
            response_model=BenchmarkHardSubReadResult, responses=_ERRS,
            summary="PC only: a title's latest hard-sub reading by speaker (progress, or the lines and comparison)")
def get_hard_sub_reading(request: Request, drama_id: int = Path(ge=1)):
    return hardsub_read_svc.result(request.state.principal, drama_id)


@router.post("/estimate", dependencies=_READ, response_model=BenchmarkEstimate,
             responses=_ERRS, summary="What a run would cost and whether the monthly cap allows it")
def post_estimate(body: BenchmarkRunRequest):
    judge, same_ok = _judge(body)
    return judge_svc.estimate_with_judge(body.stage, _configs(body), body.tier, body.set_name,
                                         body.case_ids, judge, same_ok)


@router.post("/runs", dependencies=[local_only()], response_model=BenchmarkRunStarted,
             responses=_ERRS,
             summary="PC only: start a benchmark run (two or more engines = Model Arena; confirm=true)")
def post_run(body: BenchmarkRunRequest):
    require_confirm(body.confirm)
    judge, same_ok = _judge(body)
    return judge_svc.start_run(body.stage, _configs(body), body.tier, body.set_name, body.case_ids,
                               body.label, body.prompt_version, judge, same_ok)


@router.get("/runs", dependencies=_READ, response_model=BenchmarkRunList, responses=_ERRS,
            summary="Recorded benchmark runs, newest first")
def get_runs(stage: Optional[str] = Query(default=None, max_length=20),
             limit: int = Query(default=50, ge=1, le=200)):
    out = svc.list_runs(stage, limit)
    _with_judge(out["runs"])
    return out


@router.get("/runs/{run_id}", dependencies=_READ, response_model=BenchmarkRunDetail,
            responses=_ERRS, summary="One run with its per-case results")
def get_run(run_id: int = Path(ge=1)):
    out = svc.get_run(run_id)
    _with_judge([out["run"]])
    judge_svc.annotate_results(out["run"], out["results"])
    picks_svc.annotate_results(out["run"], out["results"])
    return out


@router.get("/arena", dependencies=_READ, response_model=BenchmarkArena, responses=_ERRS,
            summary=f"Model Arena: 2-{svc.MAX_CONFIGS} runs side by side, case by case")
def get_arena(run_ids: List[int] = Query(min_length=2, max_length=svc.MAX_CONFIGS)):
    out = svc.arena(run_ids)
    _with_judge(out["runs"], out["rows"])
    return out


@router.get("/ab/next", dependencies=_READ, response_model=BenchmarkPairNext, responses=_ERRS,
            summary="Blind A/B: the next case two runs have no pick for, outputs unlabelled")
def get_ab_next(run_a: int = Query(ge=1, le=picks_svc.MAX_ROW_ID), run_b: int = Query(ge=1, le=picks_svc.MAX_ROW_ID)):
    return picks_svc.next_pair(run_a, run_b)


@router.post("/ab/pick", dependencies=[local_only()], responses=_ERRS,
             summary="PC only: record which output of a blind A/B pair reads better")
def post_ab_pick(body: BenchmarkPickRequest):
    return picks_svc.record_pick(body.ticket, body.choice)


@router.get("/ab/summary", dependencies=_READ, response_model=BenchmarkPickSummary, responses=_ERRS,
            summary="Blind A/B totals for two runs and how often the judge agreed with the owner")
def get_ab_summary(run_a: int = Query(ge=1, le=picks_svc.MAX_ROW_ID), run_b: int = Query(ge=1, le=picks_svc.MAX_ROW_ID)):
    return picks_svc.summary(run_a, run_b)


# ---- the owner's clip set, case clean-up, run delete and export --------------------------------

# 250 rows of 4000 CJK characters, JSON-escaped (\uXXXX, six bytes each), reach about 12 MB in the
# one "clips" field; the margin covers the other fields and the multipart framing.
_CLIP_ROWS_BYTES = 16 * 1024 * 1024
_CLIP_OVERHEAD = 20 * 1024 * 1024
_CLIP_FORM_HELP = ("Send multipart/form-data: 'set_name', the audio in 'files', and either 'clips' "
                   "(a JSON list of rows) or 'manifest' (JSON lines).")
_CLIP_TOO_LARGE = (f"The clips total more than {clip_svc.MAX_IMPORT_BYTES // (1024 * 1024)} MB; "
                   "import them in smaller sets.")
_CLIP_BODY = {"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
    "type": "object", "required": ["set_name", "files"],
    "properties": {"set_name": {"type": "string"}, "clips": {"type": "string"},
                   "manifest": {"type": "string"},
                   "files": {"type": "array", "items": {"type": "string", "format": "binary"}}}}}}}}


@router.post("/clips/check", dependencies=_READ, response_model=BenchmarkClipCheck,
             responses=_ERRS, summary="Check clip rows: what would refuse the import, and language warnings")
def post_clips_check(body: BenchmarkClipCheckRequest):
    return clip_svc.check_clips([c.model_dump() for c in body.clips])


def _form_text(form, name: str) -> Optional[str]:
    value = form.get(name)
    return value if isinstance(value, str) else None


def _clip_rows(form) -> list:
    clips, manifest = _form_text(form, "clips"), _form_text(form, "manifest")
    if (clips is None) == (manifest is None):
        raise InvalidInputError("Send either 'clips' or 'manifest'.")
    if manifest is not None:
        return clip_svc.parse_manifest(manifest)
    try:
        rows = json.loads(clips)
    except ValueError:
        raise InvalidInputError("'clips' isn't valid JSON.") from None
    if not isinstance(rows, list):
        raise InvalidInputError("'clips' must be a list.")
    return rows


@router.post("/clips/import", dependencies=[local_only()], response_model=BenchmarkClipImportResult,
             responses=error_responses(404, 409, 413, 422), openapi_extra=_CLIP_BODY,
             summary="PC only: upload audio clips with transcripts into a golden set")
async def post_clips_import(request: Request):
    form = await read_capped_form(
        request, clip_svc.MAX_IMPORT_BYTES + _CLIP_OVERHEAD, "the clips", _CLIP_TOO_LARGE,
        _CLIP_FORM_HELP, max_files=clip_svc.MAX_CLIPS, max_fields=4, max_part_size=_CLIP_ROWS_BYTES)
    try:
        files = {}
        for upload in form.getlist("files"):
            if not isinstance(upload, UploadFile):
                raise InvalidInputError(_CLIP_FORM_HELP)
            name = os.path.basename((upload.filename or "").replace("\\", "/"))
            if name in files:
                raise InvalidInputError(f"Two files are named {name}.")
            files[name] = upload.file
        rows = _clip_rows(form)
        return await run_in_threadpool(clip_svc.import_clips, _form_text(form, "set_name") or "", rows, files)
    finally:
        await form.close()


@router.post("/cases/{case_id}/excluded", dependencies=[local_only()], response_model=BenchmarkCase,
             responses=_ERRS, summary="PC only: drop a case out of scoring, or bring it back")
def post_case_excluded(body: BenchmarkCaseExcluded, case_id: int = Path(ge=1)):
    return clip_svc.set_excluded(case_id, body.excluded)


@router.post("/cases/{case_id}/reference", dependencies=[local_only()], response_model=BenchmarkCase,
             responses=_ERRS, summary="PC only: fix a case's transcript or translation in place (logged)")
def post_case_reference(body: BenchmarkCaseReference, case_id: int = Path(ge=1)):
    return clip_svc.edit_reference(case_id, body.reference_text)


@router.get("/cases/{case_id}/edits", dependencies=_READ, response_model=BenchmarkCaseEdits,
            responses=_ERRS, summary="When and what changed in a case's reference")
def get_case_edits(case_id: int = Path(ge=1)):
    return clip_svc.list_edits(case_id)


@router.post("/runs/{run_id}/delete", dependencies=[local_only()], response_model=BenchmarkDeleted,
             responses=_ERRS, summary="PC only: delete a run and its results (confirm=true)")
def delete_run(body: BenchmarkRunDelete, run_id: int = Path(ge=1)):
    require_confirm(body.confirm)
    out = svc.delete_run(run_id)
    return out


def _download(data: dict, fmt: str) -> Response:
    name, media_type, body = export_svc.render(data, fmt)
    return Response(body, media_type=media_type, headers={
        "Content-Disposition": f'attachment; filename="{name}"', "X-Content-Type-Options": "nosniff"})


@router.get("/runs/{run_id}/export", dependencies=_READ, responses=_ERRS,
            summary="One run's setup, scores and each case's output, as CSV or JSON")
def get_run_export(run_id: int = Path(ge=1), format: str = Query(default="csv", max_length=8)):
    return _download(export_svc.run_data(run_id), format)


@router.get("/arena/export", dependencies=_READ, responses=_ERRS,
            summary=f"A comparison of 2-{svc.MAX_CONFIGS} runs on the cases they share, as CSV or JSON")
def get_arena_export(run_ids: List[int] = Query(min_length=2, max_length=svc.MAX_CONFIGS),
                     format: str = Query(default="csv", max_length=8)):
    return _download(export_svc.comparison_data(run_ids), format)


@router.get("/sweep", dependencies=_READ, response_model=BenchmarkSweepState,
            summary="The latest settings sweep, its recommendation and the decision on it")
def get_sweep():
    return sweep_svc.get_sweep()


@router.get("/sweep/decisions", dependencies=_READ, response_model=BenchmarkSweepDecisions,
            summary="Earlier settings sweep decisions, newest first")
def get_sweep_decisions():
    return sweep_svc.list_decisions()


@router.post("/sweep/estimate", dependencies=_READ, response_model=BenchmarkSweepEstimate,
             responses=error_responses(404, 409, 422, 503),
             summary="What a settings sweep would run and roughly how long it takes; spends nothing")
def post_sweep_estimate(body: BenchmarkSweepRequest):
    return sweep_svc.estimate(body.set_name, body.language, body.use_gpu)


@router.post("/sweep", dependencies=[local_only()], response_model=BenchmarkSweepStarted,
             responses=error_responses(404, 409, 422, 503),
             summary="PC only: start or resume a settings sweep of one golden set (confirm=true)")
def post_sweep(body: BenchmarkSweepRequest):
    require_confirm(body.confirm)
    return sweep_svc.start(body.set_name, body.language, body.use_gpu)


@router.post("/sweep/decision", dependencies=[local_only()], response_model=BenchmarkSweepState,
             responses=_ERRS,
             summary="PC only: approve (saved as a setting profile) or reject the sweep's winner (confirm=true)")
def post_sweep_decision(body: BenchmarkSweepDecisionRequest):
    require_confirm(body.confirm)
    return sweep_svc.decide(body.decision, body.reason)


@router.get("/translate-sweep", dependencies=_READ, response_model=BenchmarkTranslateSweepState,
            summary="The latest translation settings sweep, its recommendation and the decision on it")
def get_translate_sweep():
    return tsweep_svc.get_sweep()


@router.get("/translate-sweep/decisions", dependencies=_READ, response_model=BenchmarkSweepDecisions,
            summary="Earlier translation sweep decisions, newest first")
def get_translate_sweep_decisions():
    return tsweep_svc.list_decisions()


@router.post("/translate-sweep/estimate", dependencies=_READ, response_model=BenchmarkTranslateSweepEstimate,
             responses=error_responses(404, 409, 422, 503),
             summary="What a translation sweep would run and what it would cost; spends nothing")
def post_translate_sweep_estimate(request: Request, body: BenchmarkTranslateSweepRequest):
    # A paid engine or an Ollama cloud tag is held to engines.paid even for an estimate, so the
    # sweep's cost and model list are not a way around the rule the run itself applies.
    require_engines_allowed(request, body.engine, model=body.model)
    return tsweep_svc.estimate(body.set_name, body.language, body.engine, body.model, body.series_id)


@router.post("/translate-sweep", dependencies=[local_only()], response_model=BenchmarkTranslateSweepStarted,
             responses=error_responses(404, 409, 422, 503),
             summary="PC only: start or resume a translation settings sweep of one golden set (confirm=true)")
def post_translate_sweep(body: BenchmarkTranslateSweepRequest):
    require_confirm(body.confirm)
    return tsweep_svc.start(body.set_name, body.language, body.engine, body.model, body.series_id)


@router.post("/translate-sweep/decision", dependencies=[local_only()], response_model=BenchmarkTranslateSweepState,
             responses=_ERRS,
             summary="PC only: approve (saved as a setting profile) or reject the translation sweep's winner (confirm=true)")
def post_translate_sweep_decision(body: BenchmarkSweepDecisionRequest):
    require_confirm(body.confirm)
    return tsweep_svc.decide(body.decision, body.reason)
