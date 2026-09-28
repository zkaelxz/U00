import type { ComponentType } from 'react'

import DubStage from './stages/DubStage'
import ExportStage from './stages/ExportStage'
import ReviewStage from './stages/ReviewStage'
import SourceStage from './stages/SourceStage'
import TranslateStage from './stages/TranslateStage'
import type { StageId } from './stages'

// One line per stage. A later slice replaces only its own import and line
// here, plus its own stages/<Name>Stage.tsx. Stages take no props: they read
// the drama id and refetch helpers from useStage() (StageContext.ts).
export const STAGE_COMPONENTS: Record<StageId, ComponentType> = {
  source: SourceStage,
  translate: TranslateStage,
  review: ReviewStage,
  dub: DubStage,
  export: ExportStage,
}
