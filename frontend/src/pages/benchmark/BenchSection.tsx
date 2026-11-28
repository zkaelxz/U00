/*
 * BenchSection: one Benchmark Lab section, a plain heading over its body. Each section is shown in
 * its own pane of the side list, so it has no fold and no box of its own.
 */
import { useId, type ReactNode } from 'react'

import { HelpTip } from '../../components/HelpTip'
import { BENCH_SECTIONS, type BenchSectionId } from './benchmarkHelp'

type Props = {
  id: BenchSectionId
  meta?: ReactNode
  actions?: ReactNode
  className?: string
  children: ReactNode
}

export function BenchSection({ id, meta, actions, className, children }: Props) {
  const copy = BENCH_SECTIONS[id]
  const helpId = `${useId()}-help`

  return (
    <section className={className ? `bench-section ${className}` : 'bench-section'} aria-label={copy.title}>
      <header className="bench-section-head">
        <div className="bench-section-heading">
          <div className="bench-section-title">
            <h2 className="section-title">{copy.title}</h2>
            <HelpTip label={copy.title} id={helpId} className="bench-help">
              {copy.steps.map((s, i) => (
                <span key={s} className="bench-help-step">
                  {i + 1}. {s}
                </span>
              ))}
            </HelpTip>
          </div>
          <p className="muted bench-section-purpose">{copy.purpose}</p>
          {meta != null && <p className="muted bench-section-meta">{meta}</p>}
        </div>
        {actions != null && <div className="bench-section-actions">{actions}</div>}
      </header>
      <div className="bench-section-body">{children}</div>
    </section>
  )
}
