import { Fragment, useCallback, useId, useState } from 'react'

import { setAdultEnabled, setSourceEnabled, setSourcePace } from '../../api/sources'
import { Badge } from '../../components/Badge'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import { buttonClass } from '../../components/uiClasses'
import type { SourceHealth, SourcePace, SourcesSettings, SourceSummary } from '../../types/sources'
import { ManageCard } from './ManageCard'
import { PaceSelect } from './PaceSelect'
import { SourceDetail } from './SourceDetail'
import { FAST_NOTE, healthText, healthTone } from './sourcesFormat'
import { settingsSummary } from './sourcesSettings'

type Props = {
  phone: boolean
  sources: SourceSummary[]
  settings: SourcesSettings | null
  onSource: (s: SourceSummary) => void
  onHealth: (name: string, h: SourceHealth) => void
}

const ADULT_HELP =
  'Sends this site\'s own "I\'m an adult" switch with its requests. Off by default; this site only.'

function SiteState({ light, extensionOnly }: { light: string; extensionOnly?: boolean }) {
  // Old request failures are not news for a site the person reads through the extension.
  if (extensionOnly) return <Badge tone="info">Extension only</Badge>
  return <Badge tone={healthTone(light)}>{healthText(light)}</Badge>
}

/** A site's on/off switch; on a phone card it also gets a visible "On" label. */
function OnToggle({ s, labelled, onChange }: { s: SourceSummary; labelled: boolean; onChange: (on: boolean) => void }) {
  const id = useId()
  const toggle = <Toggle id={id} checked={s.enabled} aria-label={`On: ${s.display_name}`} onChange={onChange} />
  if (!labelled) return toggle
  return (
    <span className="source-on">
      {toggle}
      <label htmlFor={id}>On</label>
    </span>
  )
}

/** The sites: name, state, On switch, Rules and Details. Everything else about a site is behind Details. */
export function SitesTab({ phone, sources, settings, onSource, onHealth }: Props) {
  const [error, setError] = useState<unknown>(null)
  const [open, setOpen] = useState<string | null>(null)

  async function toggle(s: SourceSummary, key: 'enabled' | 'adult_enabled', value: boolean) {
    setError(null)
    onSource({ ...s, [key]: value }) // optimistic
    try {
      onSource(key === 'enabled' ? await setSourceEnabled(s.name, value) : await setAdultEnabled(s.name, value))
    } catch (e) {
      onSource(s) // roll back
      setError(e)
    }
  }

  async function changePace(s: SourceSummary, pace: SourcePace) {
    setError(null)
    onSource({ ...s, pace }) // optimistic
    try {
      onSource(await setSourcePace(s.name, pace))
    } catch (e) {
      onSource(s) // roll back
      setError(e)
    }
  }

  const signinChanged = useCallback(
    (name: string, has: boolean) => {
      const s = sources.find((x) => x.name === name)
      if (s && s.has_saved_signin !== has) onSource({ ...s, has_saved_signin: has })
    },
    [sources, onSource],
  )
  const extensionChanged = useCallback(
    (name: string, on: boolean) => {
      const s = sources.find((x) => x.name === name)
      if (s && !!s.extension_only !== on) onSource({ ...s, extension_only: on })
    },
    [sources, onSource],
  )

  const detailsButton = (s: SourceSummary) => (
    <button
      type="button"
      className={buttonClass('secondary', 'sm')}
      aria-expanded={open === s.name}
      aria-label={`Details: ${s.display_name}`}
      onClick={() => setOpen(open === s.name ? null : s.name)}
    >
      Details
    </button>
  )
  const nameCell = (s: SourceSummary) => (
    <>
      <strong>{s.display_name}</strong>
      {s.auth_supported && s.has_saved_signin && <small className="muted source-signed-in"> Signed in</small>}
    </>
  )
  const detail = (s: SourceSummary) => (
    <div className="source-detail-wrap">
      {s.supports_adult_toggle && (
        <div className="toggle-list source-adult">
          <Field label="Adult works" help={ADULT_HELP}>
            <Toggle checked={s.adult_enabled} onChange={(on) => toggle(s, 'adult_enabled', on)} />
          </Field>
        </div>
      )}
      <SourceDetail name={s.name} onHealth={onHealth} onSignin={signinChanged} onExtensionOnly={extensionChanged} />
    </div>
  )

  return (
    <ManageCard tab="sites" card="sites" title="All sites" count={sources.length} summary={settingsSummary(settings, sources)}>
      <p className="muted" data-testid="fast-note">{FAST_NOTE}</p>
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ pcOnly: true, serverText: true }} />
      {phone ? (
        <ul className="source-cards">
          {sources.map((s) => (
            <li key={s.name}>
              <div className="source-card-line">
                <span>{nameCell(s)}</span>
                <SiteState light={s.health} extensionOnly={s.extension_only} />
              </div>
              <div className="source-card-line">
                <OnToggle s={s} labelled onChange={(on) => toggle(s, 'enabled', on)} />
                <PaceSelect s={s} onChange={(p) => changePace(s, p)} />
              </div>
              <div className="source-card-line">{detailsButton(s)}</div>
              {open === s.name && detail(s)}
            </li>
          ))}
        </ul>
      ) : (
        <div className="table-scroll">
          <table className="sources-table">
            <thead>
              <tr>
                <th scope="col">Site</th>
                <th scope="col">Status</th>
                <th scope="col">On</th>
                <th scope="col">Rules</th>
                <th scope="col">
                  <span className="visually-hidden">Details</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {sources.map((s) => (
                <Fragment key={s.name}>
                  <tr>
                    <td>{nameCell(s)}</td>
                    <td>
                      <SiteState light={s.health} extensionOnly={s.extension_only} />
                    </td>
                    <td>
                      <OnToggle s={s} labelled={false} onChange={(on) => toggle(s, 'enabled', on)} />
                    </td>
                    <td>
                      <PaceSelect s={s} onChange={(p) => changePace(s, p)} />
                    </td>
                    <td>{detailsButton(s)}</td>
                  </tr>
                  {open === s.name && (
                    <tr className="source-detail-row">
                      <td colSpan={5}>{detail(s)}</td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </ManageCard>
  )
}
