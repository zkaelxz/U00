import { useState, type ReactNode } from 'react'

import { getProfileEditorFields, setDefaultTranslatePreset } from '../../api/translateStage'
import { ErrorBanner } from '../../components/ErrorBanner'
import { buttonClass } from '../../components/uiClasses'
import { useHolds } from '../../hooks/useHolds'
import type { PcMode } from '../../hooks/usePcOnly'
import { engineLabel } from '../../labels'
import type { LibraryPreset } from '../../types/library'
import { ProfileEditor, type ProfileEditorData } from './ProfileEditor'
import { emptyProfile, fromPreset } from './profileForm'

interface ListProps {
  items: { id: number; name: string; meta: string | null }[] | undefined
  extra: (id: number, name: string) => ReactNode
}

/**
 * The saved profiles in Library tools: the list (rename and delete come from the shared list),
 * plus Edit, Make default and New profile. Editing replaces every field, so it is PC only like
 * an overwrite; Make default and New profile need only the library admin.
 */
export function ProfileTools({ presets, pc, onChanged, list }: {
  presets: LibraryPreset[] | undefined
  pc: PcMode
  onChanged: () => void
  list: (props: ListProps) => ReactNode
}) {
  const canManage = useHolds('admin.library')
  const [editor, setEditor] = useState<{ data: ProfileEditorData; existing: LibraryPreset | null } | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [error, setError] = useState<unknown>(null)

  const open = (existing: LibraryPreset | null) => {
    setError(null)
    getProfileEditorFields().then(
      (data) => setEditor({ data, existing }),
      setError,
    )
  }
  const makeDefault = (p: LibraryPreset) => {
    setBusyId(p.id)
    setError(null)
    setDefaultTranslatePreset(p.id, !p.is_default).then(
      () => { setBusyId(null); onChanged() },
      (e: unknown) => { setBusyId(null); setError(e) },
    )
  }
  const done = () => { setEditor(null); onChanged() }

  const items = presets?.map((p) => ({
    id: p.id,
    name: p.name,
    meta: [p.translation_engine ? engineLabel(p.translation_engine) : '', p.is_default ? 'default' : '']
      .filter(Boolean).join(' · ') || null,
  }))
  const extra = (id: number, name: string) => {
    const p = presets?.find((x) => x.id === id)
    if (!p || !canManage) return null
    return (
      <>
        {pc !== 'remote' && (
          <button type="button" className={buttonClass('ghost', 'sm')} aria-label={`Edit ${name}`} onClick={() => open(p)}>Edit</button>
        )}
        <button
          type="button"
          className={buttonClass('ghost', 'sm')}
          aria-label={p.is_default ? `Stop using ${name} as the default` : `Make ${name} the default`}
          disabled={busyId === id}
          onClick={() => makeDefault(p)}
        >
          {p.is_default ? 'Remove default' : 'Make default'}
        </button>
      </>
    )
  }

  return (
    <>
      {list({ items, extra })}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {editor ? (
        <ProfileEditor
          // A different profile (or New) is a different form, not an edit of the open one.
          key={editor.existing?.id ?? 'new'}
          data={editor.data}
          existing={editor.existing}
          initial={editor.existing
            ? fromPreset(editor.existing, editor.data.style_presets[0]?.key ?? '')
            : emptyProfile(editor.data.engines[0]?.name ?? '', editor.data.style_presets[0]?.key ?? '')}
          onSaved={done}
          onCancel={() => setEditor(null)}
        />
      ) : canManage && (
        <div className="actions">
          <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => open(null)}>New profile</button>
        </div>
      )}
    </>
  )
}
