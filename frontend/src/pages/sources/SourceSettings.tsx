import { Fragment, useCallback, useEffect, useId, useState } from 'react'

import { getPcMode, loadPcMode } from '../../api/pcOnly'
import {
  clearSourcesCache,
  getSourcesSettings,
  listProfiles,
  rollbackProfile,
  setAdultEnabled,
  setSourceEnabled,
  setSourcePace,
} from '../../api/sources'
import { Badge } from '../../components/Badge'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import { Toggle } from '../../components/Toggle'
import { buttonClass } from '../../components/uiClasses'
import { PC_ONLY_BODY, PC_ONLY_SUMMARY } from '../../hooks/usePcOnly'
import type { PcMode } from '../../hooks/usePcOnly'
import type { SourceHealth, SourcePace, SourceProfileDomain, SourcesSettings, SourceSummary } from '../../types/sources'
import { formatBytes } from '../libraryAdmin/libraryAdmin'
import { PaceSelect } from './PaceSelect'
import { PacingForm } from './PacingForm'
import { ProxyForm } from './ProxyForm'
import { RecentExtractions } from './RecentExtractions'
import { SourceDetail } from './SourceDetail'
import { SourceDomains } from './SourceDomains'
import { healthText, healthTone, pacingSummary, profileLine, settingsSummary } from './sourcesFormat'

type Props = {
  pc: PcMode
  phone: boolean
  sources: SourceSummary[]
  onSource: (s: SourceSummary) => void
  onHealth: (name: string, h: SourceHealth) => void
  // The Adult works switch changed for this source.
  onAdultChanged: (name: string) => void
}

const ADULT_HELP =
  'Sends this site\'s own "I\'m an adult" switch with its requests. Off by default; this source only.'

export function SourceSettings(props: Props) {
  if (props.pc === 'remote') {
    return (
      <Section title="Source settings" summary={PC_ONLY_SUMMARY}>
        <p className="muted">{PC_ONLY_BODY}</p>
      </Section>
    )
  }
  return <LocalSettings {...props} />
}

function Health({ light }: { light: string }) {
  return <Badge tone={healthTone(light)}>{healthText(light)}</Badge>
}

/** A source's on/off switch; on a phone card it also gets a visible "On" label. */
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

function LocalSettings({ phone, sources, onSource, onHealth, onAdultChanged }: Props) {
  const [settings, setSettings] = useState<SourcesSettings | null>(null)
  const [profiles, setProfiles] = useState<SourceProfileDomain[]>([])
  const [error, setError] = useState<unknown>(null)
  const [open, setOpen] = useState<string | null>(null)
  const [clearing, setClearing] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const pacingId = useId()

  // Wait for /api/meta so a remote viewer never sends a settings request.
  useEffect(() => {
    let off = false
    void loadPcMode().then(() => {
      if (off || getPcMode() === 'remote') return
      getSourcesSettings().then((s) => !off && setSettings(s), (e: unknown) => !off && setError(e))
      listProfiles().then((p) => !off && setProfiles(p), () => undefined)
    })
    return () => {
      off = true
    }
  }, [])

  async function toggle(s: SourceSummary, key: 'enabled' | 'adult_enabled', value: boolean) {
    setError(null)
    onSource({ ...s, [key]: value }) // optimistic
    try {
      const next = key === 'enabled' ? await setSourceEnabled(s.name, value) : await setAdultEnabled(s.name, value)
      onSource(next)
      if (key === 'adult_enabled') onAdultChanged(s.name)
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

  async function clearCache() {
    setError(null)
    setClearing(true)
    try {
      const cache = await clearSourcesCache()
      setSettings((cur) => (cur ? { ...cur, cache } : cur))
    } catch (e) {
      setError(e)
    } finally {
      setClearing(false)
    }
  }

  async function makeActive(domain: string, kind: string, version: number) {
    setError(null)
    setBusy(`${domain}:${kind}:${version}`)
    try {
      const versions = await rollbackProfile(domain, kind, version)
      // The reply is every version saved for the domain.
      setProfiles((ps) => ps.map((p) => (p.domain === domain ? { ...p, versions } : p)))
    } catch (e) {
      setError(e)
    } finally {
      setBusy(null)
    }
  }

  const detailsButton = (s: SourceSummary) => (
    <button
      type="button"
      className={buttonClass('secondary', 'sm')}
      aria-expanded={open === s.name}
      onClick={() => setOpen(open === s.name ? null : s.name)}
    >
      Details
    </button>
  )
  const onBox = (s: SourceSummary) => <OnToggle s={s} labelled={phone} onChange={(on) => toggle(s, 'enabled', on)} />
  const paceBox = (s: SourceSummary) => <PaceSelect s={s} onChange={(p) => changePace(s, p)} />
  const adultBox = (s: SourceSummary) =>
    s.supports_adult_toggle ? (
      <div className="toggle-list source-adult">
        <Field label="Adult works" help={ADULT_HELP}>
          <Toggle checked={s.adult_enabled} onChange={(on) => toggle(s, 'adult_enabled', on)} />
        </Field>
      </div>
    ) : null
  const signinChanged = useCallback(
    (name: string, has: boolean) => {
      const s = sources.find((x) => x.name === name)
      if (s && s.has_saved_signin !== has) onSource({ ...s, has_saved_signin: has })
    },
    [sources, onSource],
  )
  const signin = (s: SourceSummary) => (s.auth_supported ? (s.has_saved_signin ? 'Sign-in saved' : 'No sign-in') : '')
  const domains = profiles.filter((p) => p.versions.length > 0)

  return (
    <Section title="Source settings" summary={settingsSummary(settings, sources)} storageKey="sources.settings">
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ pcOnly: true, serverText: true }} />

      {phone ? (
        <ul className="source-cards">
          {sources.map((s) => (
            <li key={s.name}>
              <div className="source-card-line">
                <strong>{s.display_name}</strong>
                <Health light={s.health} />
              </div>
              <div className="source-card-line">
                {onBox(s)}
                {adultBox(s)}
              </div>
              <div className="source-card-line">{paceBox(s)}</div>
              <div className="source-card-line">
                <span className="muted">{signin(s)}</span>
                {detailsButton(s)}
              </div>
              {open === s.name && <SourceDetail name={s.name} onHealth={onHealth} onSignin={signinChanged} />}
            </li>
          ))}
        </ul>
      ) : (
        <div className="table-scroll">
          <table className="sources-table">
            <thead>
              <tr>
                <th scope="col">Source</th>
                <th scope="col">Health</th>
                <th scope="col">On</th>
                <th scope="col">Adult</th>
                <th scope="col">Pace</th>
                <th scope="col">Sign-in</th>
                <th scope="col">
                  <span className="visually-hidden">Details</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {sources.map((s) => (
                <Fragment key={s.name}>
                  <tr>
                    <td>{s.display_name}</td>
                    <td>
                      <Health light={s.health} />
                    </td>
                    <td>{onBox(s)}</td>
                    <td>{adultBox(s)}</td>
                    <td>{paceBox(s)}</td>
                    <td className="muted">{signin(s)}</td>
                    <td>{detailsButton(s)}</td>
                  </tr>
                  {open === s.name && (
                    <tr className="source-detail-row">
                      <td colSpan={7}>
                        <SourceDetail name={s.name} onHealth={onHealth} onSignin={signinChanged} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {settings && (
        <>
          <div className="sources-subsection" role="group" aria-labelledby={pacingId}>
            <h4 id={pacingId}>Pacing & cache</h4>
            <p className="muted">{pacingSummary(settings)}</p>
            <PacingForm settings={settings} onSaved={setSettings} />
          </div>

          <ProxyForm settings={settings} onSaved={setSettings} />

          <div className="actions" data-testid="sources-cache">
            <span>
              Cache:{' '}
              {settings.cache.entries
                ? `${settings.cache.entries} item${settings.cache.entries === 1 ? '' : 's'} · ${formatBytes(settings.cache.bytes)}`
                : 'empty'}
            </span>
            {settings.cache.entries > 0 && (
              <ConfirmButton label="Clear cache…" name="raw-content cache" verb="clear" busy={clearing} onConfirm={clearCache} />
            )}
          </div>
        </>
      )}

      {domains.length > 0 && (
        <Section title="Site profiles" count={domains.length} storageKey="sources.profiles">
          {domains.map((p) => (
            <div key={p.domain} className="source-profile">
              <h4>{p.domain}</h4>
              <ul className="sources-rows">
                {p.versions.map((v) => (
                  <li key={`${v.kind}:${v.version}`}>
                    <span>{profileLine(v)}</span>
                    {v.status !== 'active' && v.version !== null && v.kind && (
                      <button
                        type="button"
                        className={buttonClass('secondary', 'sm')}
                        disabled={busy !== null}
                        onClick={() => makeActive(p.domain, v.kind as string, v.version as number)}
                      >
                        Make active
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </Section>
      )}
      <SourceDomains />
      <RecentExtractions />
    </Section>
  )
}
