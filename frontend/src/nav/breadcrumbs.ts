import type { Route } from '../router'
import { routeHref } from '../router'
import { STAGE_LABELS, isStageId } from '../pages/workspace/stages'
import { NAV_ITEMS } from './navItems'

export interface Crumb {
  label: string
  /** Absent on the last crumb, which is the current page. */
  href?: string
}

export interface CrumbOptions {
  /** The title the page already loaded; a placeholder is used until it arrives. */
  title?: string | null
  /** The stage the Workspace resolved for a URL that names none. */
  stage?: string | null
}

const labelOf = (name: Route['name']): string => NAV_ITEMS.find((i) => i.target.name === name)?.label ?? name

/**
 * The trail for a nested page; empty for a top-level menu destination and for
 * any route this does not know, so those pages render no breadcrumb. Labels
 * are returned whole: cutting long ones is the stylesheet's job, so the
 * accessible name and the tooltip keep the full text.
 */
export function routeCrumbs(route: Route, opts: CrumbOptions = {}): Crumb[] {
  const library: Crumb = { label: labelOf('library'), href: routeHref({ name: 'library' }) }
  switch (route.name) {
    case 'library-tools':
      return [library, { label: labelOf('library-tools') }]
    case 'drama': {
      const stage = opts.stage ?? route.stage
      const title = { label: opts.title || `Title #${route.id}`, href: routeHref({ name: 'drama', id: route.id, stage: null }) }
      if (stage === null || !isStageId(stage)) return [library, { label: title.label }]
      return [library, title, { label: STAGE_LABELS[stage] }]
    }
    case 'read':
    case 'comic':
      return [
        library,
        { label: opts.title || `Title #${route.id}`, href: routeHref({ name: 'drama', id: route.id, stage: null }) },
        { label: route.name === 'read' ? 'Reader' : 'Comic' },
      ]
    case 'settings':
      return route.section ? [{ label: labelOf('settings'), href: routeHref({ name: 'settings' }) }, { label: 'Developer mode' }] : []
    case 'benchmark':
      return [{ label: labelOf('diagnostics'), href: routeHref({ name: 'diagnostics' }) }, { label: labelOf('benchmark') }]
    default:
      return []
  }
}
