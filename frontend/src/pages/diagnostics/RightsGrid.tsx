/** People > Rights: one grid, a row per right and a column per active member, a switch in each cell. */
import { HelpTip } from '../../components/HelpTip'
import { Toggle } from '../../components/Toggle'
import type { AdminUser } from '../../types/adminUsers'
import { personShortName, rightAbout, rightName, userName } from './adminUsers'

/**
 * Each switch gives or takes one right at once, as the tick boxes did, and is named "‹right› for ‹person›" so a
 * tap in the wrong column is announced. While one change is on its way the others ignore taps instead of being
 * disabled, so the switch in focus keeps focus.
 */
export function RightsGrid({ members, rights, canChange, pending, onRight }: {
  members: AdminUser[]
  rights: string[]
  canChange: boolean
  /** The cell being saved, "‹user id›:‹right›". */
  pending: string | null
  onRight: (user: AdminUser, permission: string, granted: boolean) => void
}) {
  if (members.length === 0 || rights.length === 0) return null
  return (
    <div className="admin-rights" data-testid="admin-rights">
      <h3 className="subhead" id="admin-rights-title">Rights</h3>
      <div className="admin-rights-scroll">
        <table className="admin-rights-table" aria-labelledby="admin-rights-title" aria-busy={pending !== null || undefined}>
          <thead>
            <tr>
              <th scope="col"><span className="visually-hidden">Right</span></th>
              {members.map((m) => <th key={m.id} scope="col" title={m.email}>{personShortName(m)}</th>)}
            </tr>
          </thead>
          <tbody>
            {rights.map((p) => {
              const about = rightAbout(p)
              return (
                <tr key={p}>
                  <th scope="row">
                    <span className="admin-right-name">{rightName(p)}</span>
                    {about && <HelpTip label={rightName(p)} id={`admin-right-${p}`}>{about}</HelpTip>}
                  </th>
                  {members.map((m) => {
                    const on = m.permissions.includes(p)
                    const cell = `${m.id}:${p}`
                    return (
                      <td key={m.id}>
                        <Toggle checked={on} disabled={!canChange} aria-label={`${rightName(p)} for ${userName(m)}`}
                          aria-busy={pending === cell || undefined}
                          onChange={(next) => { if (pending === null) onRight(m, p, next) }} />
                      </td>
                    )
                  })}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
