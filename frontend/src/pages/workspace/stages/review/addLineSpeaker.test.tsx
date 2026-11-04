import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { DramaDetail } from '../../../../api/types'
import type { ReviewLine } from '../../../../types/review'
import { StageContext } from '../../StageContext'
import { AddLineForm } from './AddLineForm'

const prev: ReviewLine = {
  id: 1, idx: 0, start: 1, end: 2, zh: '', en: '', speaker: 'Mei', speaker_manual: false,
  sfx: false, flag: null, flag_note: null, dub_filename: null, lang: null, reviewed: false, confidence: null,
}

function render(dramaId?: number) {
  const form = <AddLineForm dramaId={dramaId} after={prev} next={null} busy={false} blocked={null} onAdd={() => {}} />
  return renderToStaticMarkup(
    <StageContext.Provider value={{ dramaId: 1, drama: {} as DramaDetail, refetchDrama: () => {}, onJobDone: () => {}, simple: false }}>{form}</StageContext.Provider>,
  )
}

// The loaded dropdown needs an effect plus a fetch, which the node test environment cannot run;
// these cover the first paint, which is the text-box fallback SpeakerSelect shows until the speakers load.
describe('speaker field in the Add-line form', () => {
  it('starts from the previous line speaker with or without a title id', () => {
    expect(render(5)).toContain('value="Mei"')
    expect(render()).toContain('value="Mei"')
  })
})
