import type { DiagnosticsModelCache } from '../../types/diagnostics'
import { Section } from '../../components/Section'
import { formatBytes } from '../libraryAdmin/libraryAdmin'
import { hasModelCache, modelCacheSummary } from './diagnosticsAdmin'

/** "Model cache": Hugging Face downloads and Piper voices, read-only. Hidden when both are empty. */
export function ModelCacheSection({ cache }: { cache: DiagnosticsModelCache | null }) {
  if (!cache || !hasModelCache(cache)) return null
  return (
    <Section title="Model cache" storageKey="diagnostics.cache" summary={modelCacheSummary(cache)}>
      {cache.hf_cache.length > 0 && (
        <ul className="diag-rows" aria-label="Downloaded models">
          {cache.hf_cache.map((e) => (
            <li key={`${e.repo_id}@${e.revision}`}>
              {e.repo_id} ({e.repo_type}) · {formatBytes(e.size_bytes)} · <code>{e.revision.slice(0, 12)}</code>
            </li>
          ))}
        </ul>
      )}
      {cache.piper_voices.length > 0 && (
        <>
          <h4>Piper voices</h4>
          <ul className="diag-rows" aria-label="Piper voices">
            {cache.piper_voices.map((v) => (
              <li key={v.voice}>
                {v.voice} · {formatBytes(v.size_bytes)}
              </li>
            ))}
          </ul>
        </>
      )}
    </Section>
  )
}
