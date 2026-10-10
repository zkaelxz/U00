# frontend/src/pages/workspace/stages/

The Workspace stages. `../stageRegistry.ts` maps each stage id to its
component: `SourceStage`, `TranslateStage`, `ReviewStage` (with `review/`),
`DubStage`, `ExportStage`. Transcription renders inside `SourceStage`
(`TranscribeStage.tsx`). Stages take no props; they read the drama id from
`useStage()` (`../StageContext.ts`).

## Start here
- `python tools/repo_map.py frontend/src/pages/workspace/stages` (or `.../stages/review`).
- Panels are `PascalCase.tsx`; their pure logic is a `camelCase.ts` beside them with a `.test.ts`.
- HTTP calls go through `frontend/src/api/<area>.ts`; types in `frontend/src/types/<area>.ts`.

## Rules
- Keep logic in the `.ts` helper so vitest can cover it without rendering.
- Phone layouts matter: many flows have a `.mobile.spec.ts` in `frontend/e2e/`.

## Tests
- `cd frontend && npx tsc --noEmit && npx vitest run`
- `frontend/e2e/<stage>-*.spec.ts`, e.g. `dub-stage.spec.ts`, `export-stage.spec.ts`

## Do not touch
- `../stageRegistry.ts` lines for other stages.
