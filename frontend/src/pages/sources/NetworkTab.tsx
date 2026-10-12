import { useEffect, useState } from 'react'

import { getPcMode, loadPcMode } from '../../api/pcOnly'
import { listProfiles, rollbackProfile } from '../../api/sources'
import { ErrorBanner } from '../../components/ErrorBanner'
import { buttonClass } from '../../components/uiClasses'
import type { SourceProfileDomain, SourcesSettings } from '../../types/sources'
import { ManageCard } from './ManageCard'
import { ProxyForm } from './ProxyForm'
import { SourceDomains } from './SourceDomains'
import { profileLine } from './sourcesSettings'

/** How requests leave this PC: the proxy, each site's web addresses and the page layouts Baihe learned. */
export function NetworkTab({ settings, onSaved }: { settings: SourcesSettings; onSaved: (s: SourcesSettings) => void }) {
  const [profiles, setProfiles] = useState<SourceProfileDomain[]>([])
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState<string | null>(null)

  useEffect(() => {
    let off = false
    void loadPcMode().then(() => {
      if (off || getPcMode() === 'remote') return
      listProfiles().then((p) => !off && setProfiles(p), () => undefined)
    })
    return () => {
      off = true
    }
  }, [])

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

  const domains = profiles.filter((p) => p.versions.length > 0)

  return (
    <>
      <ProxyForm settings={settings} onSaved={onSaved} />
      <SourceDomains />
      {domains.length > 0 && (
        <ManageCard tab="network" card="layouts" title="Saved page layouts" count={domains.length}>
          <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ pcOnly: true, serverText: true }} />
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
        </ManageCard>
      )}
    </>
  )
}
