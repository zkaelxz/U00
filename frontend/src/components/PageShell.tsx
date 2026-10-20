/*
 * PageShell: the frame every top-level page (not a title's workspace stage) renders inside, so width,
 * header and spacing are decided in one place.
 *
 *   <PageShell width="narrow" title="Settings" meta="Engines, keys and preferences" tabs={<Tabs … />}>
 *     <Card>…</Card>
 *   </PageShell>
 *
 * width    narrow, standard or wide. All three are the one --content-max column now, so every page has the same
 *          width and left edge; a page picks one and does not add its own max-width.
 * title    the page heading (h1.page-title, the page's one h1: the brand in the header is not a heading). `meta` is the line under it (text, badges or both);
 *          `actions` sits at the header's right; `back` is a breadcrumb or back link above the header.
 * tabs     anything that belongs between the header and the content (a tab bar, a search field).
 * children the content. Direct children are spaced by --page-card-gap, so a page does not add margins.
 *
 * The header-to-content gap is --page-gap; side padding and top spacing come from the app column
 * (.app-main), which every page shares.
 */
import type { ReactNode } from 'react'
import { pageShellClass, type PageShellWidth } from './pageShellWidth'

export function PageShell({ width, title, meta, actions, back, tabs, className, label, headExtra, children }: {
  width: PageShellWidth
  title: ReactNode
  meta?: ReactNode
  actions?: ReactNode | false
  back?: ReactNode
  tabs?: ReactNode | false
  className?: string
  /** Names the page as a region. Optional: a page whose content already carries that region leaves it out. */
  label?: string
  /** Extra header content under the meta line (a status line, a notice). */
  headExtra?: ReactNode
  children?: ReactNode
}) {
  return (
    <section className={pageShellClass(width, className)} data-page-shell={width} aria-label={label}>
      {back}
      <header className="page-head" data-page-header>
        <div className="page-head-text">
          <h1 className="page-title">{title}</h1>
          {meta != null && (typeof meta === 'string' ? <p className="page-meta">{meta}</p> : meta)}
          {headExtra}
        </div>
        {actions != null && actions !== false && <div className="actions">{actions}</div>}
      </header>
      {tabs != null && tabs !== false && <div className="page-shell-tabs" data-page-tabs>{tabs}</div>}
      <div className="page-shell-body" data-page-body>{children}</div>
    </section>
  )
}
