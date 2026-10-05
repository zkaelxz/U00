import type { Crumb } from './breadcrumbs'
import './breadcrumbs.css'

export function Breadcrumbs({ crumbs }: { crumbs: Crumb[] }) {
  if (crumbs.length === 0) return null
  return (
    <nav aria-label="Breadcrumb" className="crumbs">
      <ol>
        {crumbs.map((c, i) => {
          const last = i === crumbs.length - 1
          return (
            <li key={i} className={last ? 'crumb crumb-current' : 'crumb'}>
              {last || !c.href ? (
                <span aria-current={last ? 'page' : undefined} title={c.label}>{c.label}</span>
              ) : (
                <a href={c.href} title={c.label}>{c.label}</a>
              )}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}
