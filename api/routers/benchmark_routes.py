"""
api/routers/benchmark_routes.py -- Step 38: the Benchmark Lab (golden sets,
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
"""

from typing import List, Optional

from fastapi import APIRouter, Path, Query

from api.auth import local_only, require_permission
from api.benchmark_schemas import (BenchmarkArena, BenchmarkCase, BenchmarkCaseCreate,
                                   BenchmarkCaseDelete, BenchmarkCaseList, BenchmarkDeleted,
                                   BenchmarkEstimate, BenchmarkImportRequest,
                                   BenchmarkImportResult, BenchmarkOptions,
                                   BenchmarkRegressionResult, BenchmarkRunDetail,
                                   BenchmarkRunList, BenchmarkRunRequest,
                                   BenchmarkRunStarted, BenchmarkSetList)
from api.routers.settings_routes import _require_confirm
from api.schemas import ErrorResponse
from services import benchmark_lab_service as svc

router = APIRouter(prefix="/api/benchmark", tags=["benchmark"])

_ERRS = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
         422: {"model": ErrorResponse}}
_READ = [require_permission("admin.diagnostics")]


def _configs(body: BenchmarkRunRequest) -> list:
    return [c.model_dump() for c in body.configs]


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
    _require_confirm(body.confirm)
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


@router.post("/estimate", dependencies=_READ, response_model=BenchmarkEstimate,
             responses=_ERRS, summary="What a run would cost and whether the monthly cap allows it")
def post_estimate(body: BenchmarkRunRequest):
    return svc.estimate(body.stage, _configs(body), body.tier, body.set_name, body.case_ids)


@router.post("/runs", dependencies=[local_only()], response_model=BenchmarkRunStarted,
             responses=_ERRS,
             summary="PC only: start a benchmark run (two or more engines = Model Arena; confirm=true)")
def post_run(body: BenchmarkRunRequest):
    _require_confirm(body.confirm)
    return svc.start_run(body.stage, _configs(body), body.tier, body.set_name, body.case_ids,
                         body.label, body.prompt_version)


@router.get("/runs", dependencies=_READ, response_model=BenchmarkRunList, responses=_ERRS,
            summary="Recorded benchmark runs, newest first")
def get_runs(stage: Optional[str] = Query(default=None, max_length=20),
             limit: int = Query(default=50, ge=1, le=200)):
    return svc.list_runs(stage, limit)


@router.get("/runs/{run_id}", dependencies=_READ, response_model=BenchmarkRunDetail,
            responses=_ERRS, summary="One run with its per-case results")
def get_run(run_id: int = Path(ge=1)):
    return svc.get_run(run_id)


@router.get("/arena", dependencies=_READ, response_model=BenchmarkArena, responses=_ERRS,
            summary="Model Arena: 2-4 runs side by side, case by case")
def get_arena(run_ids: List[int] = Query(min_length=2, max_length=4)):
    return svc.arena(run_ids)
