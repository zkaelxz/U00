/*
 * BenchSection: a Benchmark Lab card whose heading folds it. The heading is a real button
 * (aria-expanded), with the one-line purpose and the (i) help beside it.
 *
 * The body is hidden, not unmounted, so form entries and polling survive a fold. A running job's
 * `status` shows on the heading while folded; the section is not forced open, so the viewer's choice holds.
 * Open/closed is remembered per viewer under "baihe.section.benchmark.card.<id>".
 */
import { useId, useState, type ReactNode } from 'react'

import { HelpTip } from '../../components/HelpTip'
import { writeSectionOpen } from '../../components/sectionStorage'
import { browserStorage } from '../../hooks/usePersistedState'
import { BENCH_SECTIONS, type BenchSectionId } from './benchmarkHelp'
import { benchSectionOpen, benchSectionStorageKey } from './benchmarkForm'

type Props = {
  id: BenchSectionId
  meta?: ReactNode
  actions?: ReactNode
  // One line shown on the heading while a job runs.
  status?: string | null
  className?: string
  children: ReactNode
}

export function BenchSection({ id, meta, actions, status, className, children }: Props) {
  const copy = BENCH_SECTIONS[id]
  const base = useId()
  const bodyId = `${base}-body`
  const helpId = `${base}-help`
  const [open, setOpen] = useState(() => benchSectionOpen(browserStorage(), id))
  const toggle = () => {
    writeSectionOpen(browserStorage(), benchSectionStorageKey(id), !open)
    setOpen(!open)
  }

  return (
    <section className={className ? `card bench-section ${className}` : 'card bench-section'} aria-label={copy.title}>
      <header className="card-head">
        <div className="card-heading">
          <div className="bench-section-title">
            <h3 className="card-title">
              <button type="button" className="bench-section-toggle" aria-expanded={open} aria-controls={bodyId} onClick={toggle}>
                {copy.title}
              </button>
            </h3>
            <HelpTip label={copy.title} id={helpId} className="bench-help">
              {copy.steps.map((s, i) => (
                <span key={s} className="bench-help-step">
                  {i + 1}. {s}
                </span>
              ))}
            </HelpTip>
          </div>
          <p className="card-meta">{copy.purpose}</p>
          {meta != null && <p className="card-meta bench-section-meta">{meta}</p>}
          {status && !open && (
            <p className="bench-section-status" role="status" data-testid={`bench-status-${id}`}>
              {status}
            </p>
          )}
        </div>
        {actions != null && <div className="card-actions">{actions}</div>}
      </header>
      <div className="card-body bench-section-body" id={bodyId} hidden={!open}>
        {children}
      </div>
    </section>
  )
}
