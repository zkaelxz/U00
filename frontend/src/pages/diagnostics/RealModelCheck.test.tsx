import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { RealModelCheck, RealModelCheckResults } from './RealModelCheck'

describe('RealModelCheckResults', () => {
  it('lists each check with its status and reason', () => {
    const html = renderToStaticMarkup(
      <RealModelCheckResults checks={[
        { id: 'asr', label: 'Transcription', status: 'pass', reason: 'whisper ran on the GPU.' },
        { id: 'ocr', label: 'OCR', status: 'skipped', reason: 'paddleocr is not installed.' },
        { id: 'translate', label: 'Translation (Ollama)', status: 'fail', reason: 'Ollama took too long.' },
      ]} />,
    )
    expect(html).toContain('Passed')
    expect(html).toContain('Skipped')
    expect(html).toContain('Failed')
    expect(html).toContain('paddleocr is not installed.')
  })

  it('shows could-not-check as its own status, not as skipped or passed', () => {
    const html = renderToStaticMarkup(
      <RealModelCheckResults checks={[
        { id: 'ocr', label: 'OCR', status: 'could_not_check', reason: 'Could not check where the models are.' },
      ]} />,
    )
    expect(html).toContain('Could not check')
    expect(html).not.toContain('Passed')
    expect(html).not.toContain('Skipped')
  })
})

describe('RealModelCheck', () => {
  it('says it is PC only from a remote tab, without a run button', () => {
    const html = renderToStaticMarkup(<RealModelCheck pc="remote" jobsActive={false} />)
    expect(html).toContain('Real-model check')
    expect(html).not.toContain('Run real-model check')
  })
})
