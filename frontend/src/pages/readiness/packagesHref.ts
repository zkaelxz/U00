// The Diagnostics > Packages link for a missing need.
import { routeHref } from '../../router'
import type { Need } from './needs'

/** Where Diagnostics > Packages opens with the first missing package already picked. */
export const packagesHref = (need: Pick<Need, 'packages'>) =>
  routeHref({ name: 'diagnostics', card: 'packages', ...(need.packages[0] ? { pkg: need.packages[0] } : {}) })
