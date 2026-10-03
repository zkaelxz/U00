/*
 * Saved manga (#/manga, #/manga/<source>/<series>): the chapters saved as
 * CBZ files (Sources "Save as CBZ", or a tracked series' auto-save), read in
 * the app. The list shows each series with a Continue link to where this
 * browser left off; a series shows its chapters. On the main PC the save
 * folder card picks and opens the folder.
 */
import { useCallback, useEffect, useState } from 'react'

import { listSavedChapters, listSavedSeries } from '../api/savedComics'
import { ButtonLink } from '../components/Button'
import { ErrorBanner } from '../components/ErrorBanner'
import type { SavedChapterList, SavedSeries } from '../types/savedComics'
import { SaveFolderCard } from './manga/SaveFolder'
import { chapterCount, loadLastRead, mangaReadHref, mangaSeriesHref, type LastRead } from './manga/mangaLogic'
import { ago, isoTime } from './sources/sourcesFormat'
import './manga/manga.css'

function browserStorage() {
  try {
    return window.localStorage
  } catch {
    return null
  }
}

const lastReadOf = (source: string, series: string) => loadLastRead(browserStorage(), source, series)

function continueHref(source: string, series: string, last: LastRead | null) {
  return last ? mangaReadHref(source, series, last.chapter, last.page) : null
}

function SeriesList() {
  const [items, setItems] = useState<SavedSeries[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  useEffect(() => {
    listSavedSeries().then(setItems, setError)
  }, [])
  return (
    <>
      <header className="page-head">
        <div className="page-head-text">
          <h2 className="page-title">Saved manga</h2>
          <p className="page-meta">Chapters saved as CBZ files, to read here or in any comic reader.</p>
        </div>
      </header>
      <ErrorBanner error={error} />
      {items === null ? (
        !error && <p className="muted">Loading…</p>
      ) : items.length === 0 ? (
        <p className="muted" data-testid="manga-empty">
          No saved chapters yet. In <a href="#/sources">Sources</a>, open a comic series and use Save as CBZ, or
          turn on “Save new chapters as CBZ” for a tracked series.
        </p>
      ) : (
        <ul className="manga-series" aria-label="Saved series">
          {items.map((s) => {
            const last = lastReadOf(s.source, s.series)
            const resume = continueHref(s.source, s.series, last)
            return (
              <li key={`${s.source}/${s.series}`} className="card">
                <div>
                  <a className="manga-series-title" href={mangaSeriesHref(s.source, s.series)}>{s.series}</a>
                  <p className="muted">
                    {s.source} · {chapterCount(s.chapter_count)}
                    {s.updated_at !== null && (
                      <>
                        {' '}· last saved <time dateTime={isoTime(s.updated_at)}>{ago(s.updated_at)}</time>
                      </>
                    )}
                  </p>
                </div>
                <div className="actions">
                  {resume && <ButtonLink size="sm" variant="primary" href={resume}>Continue</ButtonLink>}
                  <ButtonLink size="sm" href={mangaSeriesHref(s.source, s.series)}>Chapters</ButtonLink>
                </div>
              </li>
            )
          })}
        </ul>
      )}
      <SaveFolderCard />
    </>
  )
}

function ChapterList({ source, series }: { source: string; series: string }) {
  const [data, setData] = useState<SavedChapterList | null>(null)
  const [error, setError] = useState<unknown>(null)
  const load = useCallback(() => listSavedChapters(source, series).then(setData, setError), [source, series])
  useEffect(() => {
    void load()
  }, [load])
  const last = lastReadOf(source, series)
  const resume = continueHref(source, series, last)
  return (
    <>
      <nav aria-label="Breadcrumb" className="reader-crumbs">
        <a href="#/manga">Saved manga</a>
        <span aria-hidden="true"> / </span>
        <span>{series}</span>
      </nav>
      <header className="page-head">
        <div className="page-head-text">
          <h2 className="page-title">{series}</h2>
          <p className="page-meta">
            {source}
            {data && ` · ${chapterCount(data.chapters.length)}`}
          </p>
        </div>
        <div className="actions">
          {resume ? (
            <ButtonLink variant="primary" href={resume}>Continue</ButtonLink>
          ) : (
            data?.chapters[0] && (
              <ButtonLink variant="primary" href={mangaReadHref(source, series, data.chapters[0].chapter)}>Start reading</ButtonLink>
            )
          )}
        </div>
      </header>
      <ErrorBanner error={error} />
      {data === null ? (
        !error && <p className="muted">Loading…</p>
      ) : (
        <ol className="manga-chapters" aria-label="Chapters">
          {data.chapters.map((c) => (
            <li key={c.chapter}>
              <a href={mangaReadHref(source, series, c.chapter)}>{c.title}</a>
              {last?.chapter === c.chapter && <span className="muted"> · reading, page {last.page}</span>}
            </li>
          ))}
        </ol>
      )}
    </>
  )
}

export default function SavedMangaPage({ source, series }: { source?: string; series?: string }) {
  return (
    <main className="manga-page">
      {source !== undefined && series !== undefined ? <ChapterList source={source} series={series} /> : <SeriesList />}
    </main>
  )
}
