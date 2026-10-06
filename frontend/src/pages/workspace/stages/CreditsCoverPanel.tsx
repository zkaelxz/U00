import { useState } from 'react'

import { coverUrl, romanizeCredits, uploadCover } from '../../../api/metadata'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { humanize } from '../../../components/labels'
import { buttonClass } from '../../../components/uiClasses'
import { usePcOnly } from '../../../hooks/usePcOnly'
import { COVER_ACCEPT, coverFileProblem, creditRows, hasCredits } from '../preambleForm'
import { useStage } from '../StageContext'
import './preamble.css'

/**
 * Romanize and cover upload inside Edit details (inventory P13, P14). Romanize asks the drama's
 * translation engine for readable forms of the author, studio, director and
 * cast; the originals are kept and both are shown. The cover upload is PC
 * only; the server accepts PNG, JPEG or WebP up to 10 MB and strips metadata.
 */
export function CreditsCover({ onAddCredits }: { onAddCredits?: () => void }) {
  const { dramaId, drama, refetchDrama } = useStage()
  const pc = usePcOnly()
  const [romanizing, setRomanizing] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [fileKey, setFileKey] = useState(0)
  const [uploading, setUploading] = useState(false)
  const [coverVersion, setCoverVersion] = useState(0)
  const [coverBroken, setCoverBroken] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)

  // The originals are in the Author, Studio, Director and Voice actors fields; only romanized forms are new.
  const rows = creditRows(drama).filter((r) => r.romanized)
  const canRomanize = hasCredits(drama)
  const problem = coverFileProblem(file)
  const engine = humanize('engine', drama.translation_engine || 'claude')

  const romanize = () => {
    setRomanizing(true)
    setError(null)
    setNotice(null)
    romanizeCredits(dramaId).then(
      (r) => {
        setRomanizing(false)
        setNotice(r.updated ? 'Credits romanized; the originals are kept alongside.' : "Couldn't romanize those credits.")
        refetchDrama()
      },
      (e: unknown) => {
        setRomanizing(false)
        setError(e)
      },
    )
  }

  const upload = () => {
    if (!file || problem) return
    setUploading(true)
    setError(null)
    setNotice(null)
    uploadCover(dramaId, file).then(
      (r) => {
        setUploading(false)
        setFile(null)
        setFileKey((k) => k + 1)
        setCoverBroken(false)
        setCoverVersion(Date.now())
        setNotice(`Cover saved (${r.width}×${r.height}).`)
        refetchDrama()
      },
      (e: unknown) => {
        setUploading(false)
        setError(e)
      },
    )
  }

  return (
        <div className="source-panel credits-cover">
          {rows.length > 0 && (
            <dl className="credit-rows" data-testid="credits" aria-label="Romanized credits">
              {rows.map((r) => (
                <div key={r.key}>
                  <dt>{r.label}</dt>
                  <dd>{r.text}</dd>
                </div>
              ))}
            </dl>
          )}
          {canRomanize ? (
            <div>
              <button type="button" disabled={romanizing} onClick={romanize}>
                {romanizing ? 'Romanizing…' : 'Romanize credits'}
              </button>
              <p className="muted">
                {`Uses this drama's translation engine (${engine}). Names are romanized; studios keep an official English name when there is one.`}
              </p>
            </div>
          ) : (
            <p className="muted source-needed">
              <span>No credits yet. Still needed: an author, studio, director or cast.</span>
              {onAddCredits && (
                <button type="button" className={buttonClass('ghost', 'sm')} onClick={onAddCredits}>
                  Add credits
                </button>
              )}
            </p>
          )}

          <div className="cover-row">
            {drama.has_cover_art && !coverBroken ? (
              <img
                className="cover-preview"
                src={coverUrl(dramaId, coverVersion)}
                alt={`Cover of ${drama.title_en || drama.title_zh || `drama #${dramaId}`}`}
                onError={() => setCoverBroken(true)}
              />
            ) : (
              <p className="muted">{drama.has_cover_art ? "The cover couldn't be shown." : 'No cover.'}</p>
            )}
            {pc === 'remote' ? (
              <p className="muted">Uploading a cover is PC only.</p>
            ) : (
              <div className="cover-upload">
                <Field label="Cover image" help="PNG, JPEG or WebP, up to 10 MB. Location and camera details are removed.">
                  <input
                    key={fileKey}
                    type="file"
                    accept={COVER_ACCEPT}
                    onChange={(e) => {
                      setNotice(null)
                      setFile(e.target.files?.[0] ?? null)
                    }}
                  />
                </Field>
                {problem && <p className="error" role="alert">{problem}</p>}
                <div>
                  <button type="button" disabled={!file || !!problem || uploading} onClick={upload}>
                    {uploading ? 'Uploading…' : drama.has_cover_art ? 'Replace cover' : 'Upload cover'}
                  </button>
                </div>
              </div>
            )}
          </div>
          {notice && <p role="status">{notice}</p>}
          <ErrorBanner error={error} describe={{ pcOnly: true }} onDismiss={() => setError(null)} />
        </div>
  )
}
