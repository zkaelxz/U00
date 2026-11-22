import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { SourceMatchModelView } from './SourceMatchModel'

const model = (downloaded: boolean) => ({ id: 'bge_m3', label: 'bge-m3', downloaded, job_id: 'model_download_bge_m3' })

describe('SourceMatchModelView', () => {
  it('says the model is installed and offers no download', () => {
    const html = renderToStaticMarkup(<SourceMatchModelView model={model(true)} onFinished={() => {}} />)
    expect(html).toContain('is installed')
    expect(html).not.toContain('Download')
  })

  it('says the check does nothing without the model and offers the download', () => {
    const html = renderToStaticMarkup(<SourceMatchModelView model={model(false)} onFinished={() => {}} />)
    expect(html).toContain('does nothing until it is')
    expect(html).toContain('Download')
  })
})
