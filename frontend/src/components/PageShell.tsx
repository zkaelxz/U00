/*
 * PageShell: the frame every top-level page (not a title's workspace stage) renders inside, so width,
 * header and spacing are decided in one place.
 *
 *   <PageShell width="narrow" title="Settings" meta="Engines, keys and preferences" tabs={<Tabs … />}>
 *     <Card>…</Card>
 *   </PageShell>
 *
 * width    narrow (860px: forms and settings-like pages), standard (1120px: tools and lists) or wide
 *          (the whole app column: dense tables and catalogues). A page picks one of the three; it does
 *          not add its own max-width.
 * title    the page heading (h2.page-title). `meta` is the line under it (text, badges or both);
 *          `actions` sits at the header's right; `back` is a breadcrumb or back link above the header.
 * tabs     anything that belongs between the header and the content (a tab bar, a search field).
 * children the content. Direct children are spaced by --page-card-gap, so a page does not add margins.
 *
 * The header-to-content gap is --page-gap; side padding and top spacing come from the app column
 * (.app-main), which every page shares.
 */
import type { ReactNode } from 'react'
import { pageShellClass, type PageWidth } from './pageShellWidth'

export function PageShell({ as: Tag = 'section', width, title, meta, actions, back, tabs, className, label, headExtra, children }: {
  /** `main` for the pages that were the app's main landmark before the shell existed. */
  as?: 'section' | 'main'
  width: PageWidth
  title: ReactNode
  meta?: ReactNode
  actions?: ReactNode | false
  back?: ReactNode
  tabs?: ReactNode | false
  className?: string
  /** Accessible name of the page region; defaults to the heading text when `title` is a string. */
  label?: string
  /** Extra header content under the meta line (a status line, a notice). */
  headExtra?: ReactNode
  children?: ReactNode
}) {
  const name = label ?? (typeof title === 'string' ? title : undefined)
  return (
    <Tag className={pageShellClass(width, className)} data-page-shell={width} aria-label={name}>
      {back}
      <header className="page-head" data-page-header>
        <div className="page-head-text">
          <h2 className="page-title">{title}</h2>
          {meta != null && (typeof meta === 'string' ? <p className="page-meta">{meta}</p> : meta)}
          {headExtra}
        </div>
        {actions != null && actions !== false && <div className="actions">{actions}</div>}
      </header>
      {tabs != null && tabs !== false && <div className="page-shell-tabs" data-page-tabs>{tabs}</div>}
      <div className="page-shell-body" data-page-body>{children}</div>
    </Tag>
  )
}
