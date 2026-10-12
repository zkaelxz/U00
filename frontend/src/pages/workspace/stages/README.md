# frontend/src/pages/workspace/stages/

The Workspace stages. `../stageRegistry.ts` maps each stage id to its
component: `SourceStage`, `TranslateStage`, `ReviewStage` (with `review/`),
`DubStage`, `ExportStage`. `SourceStage` (Media) is five sub-tabs through the
shared `Tabs` (`../mediaTabs.ts`: Add media, Transcribe, Tools, Details, Credits; a novel or comic has
only Add media, Details and Credits). `TranslateStage` is four sub-tabs the same way (`../translateTabs.ts`:
Translate, Glossary, Characters, Reference novel), reusing `MediaTabPanel` with its own id prefix. `TranscribeStage.tsx` owns the Transcribe and Tools panels, and
`TranscribeAdvanced.tsx` the Advanced options folds (`advancedGroups.ts` says which control sits
in which fold). `../mediaTypeSettings.ts` is the one table of which setting groups each title kind has: a
setting a kind lacks is not rendered, and the tabs and `advancedGroups` read it. Stages take no props; they read the drama id from
`useStage()` (`../StageContext.ts`).

## Start here
- `python tools/repo_map.py frontend/src/pages/workspace/stages` (or `.../stages/review`).
- Panels are `PascalCase.tsx`; their pure logic is a `camelCase.ts` beside them with a `.test.ts`.
- HTTP calls go through `frontend/src/api/<area>.ts`; types in `frontend/src/types/<area>.ts`.

## Rules
- Keep logic in the `.ts` helper so vitest can cover it without rendering.
- Phone layouts matter: many flows have a `.mobile.spec.ts` in `frontend/e2e/`.

## Tests
- `cd frontend && npx tsc -b && npx vitest run`
- `frontend/e2e/<stage>-*.spec.ts`, e.g. `dub-stage.spec.ts`, `export-stage.spec.ts`

## Do not touch
- `../stageRegistry.ts` lines for other stages.
