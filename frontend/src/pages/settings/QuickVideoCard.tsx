/*
 * Settings > Quick video translate: the profile the "From a link" button on Make subtitles uses.
 * Profiles are the saved translation profiles; New and Edit open the same editor as Library tools.
 */
import { useEffect, useState } from 'react'

import { getQuickVideoSettings, setQuickVideoProfile } from '../../api/quickVideo'
import { getPresets } from '../../api/library'
import { getProfileEditorFields } from '../../api/translateStage'
import { Card } from '../../components/Card'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'
import { useHolds } from '../../hooks/useHolds'
import type { LibraryPreset } from '../../types/library'
import type { QuickVideoSettings } from '../../types/quickVideo'
import { ProfileEditor, type ProfileEditorData } from '../libraryTools/ProfileEditor'
import { emptyProfile, fromPreset } from '../libraryTools/profileForm'

export function QuickVideoCard() {
  const canManage = useHolds('admin.library')
  const [settings, setSettings] = useState<QuickVideoSettings | null>(null)
  const [presets, setPresets] = useState<LibraryPreset[]>([])
  const [editor, setEditor] = useState<{ data: ProfileEditorData; existing: LibraryPreset | null } | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    Promise.all([getQuickVideoSettings(), getPresets()]).then(
      ([s, p]) => { setSettings(s); setPresets(p.items) },
      setError,
    )
  }, [reload])

  const open = (existing: LibraryPreset | null) => {
    setError(null)
    getProfileEditorFields().then((data) => setEditor({ data, existing }), setError)
  }
  const choose = (value: string) => {
    setError(null)
    setQuickVideoProfile(value ? Number(value) : null).then(setSettings, setError)
  }
  const chosen = presets.find((p) => p.id === settings?.profile_id) ?? null

  return (
    <Card title="Quick video translate" meta="Profile for the link button" aria-label="Quick video translate">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <Field label="Profile">
        <select value={settings?.profile_id ?? ''} disabled={!canManage || !settings} onChange={(e) => choose(e.target.value)}>
          <option value="">None</option>
          {settings?.profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </Field>
      {canManage && !editor && (
        <div className="actions">
          {chosen && <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => open(chosen)}>Edit profile</button>}
          <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => open(null)}>New profile</button>
        </div>
      )}
      {editor && (
        <ProfileEditor
          key={editor.existing?.id ?? 'new'}
          data={editor.data}
          existing={editor.existing}
          initial={editor.existing
            ? fromPreset(editor.existing, editor.data.style_presets[0]?.key ?? '')
            : emptyProfile(editor.data.engines[0]?.name ?? '', editor.data.style_presets[0]?.key ?? '')}
          onSaved={() => { setEditor(null); setReload((n) => n + 1) }}
          onCancel={() => setEditor(null)}
        />
      )}
    </Card>
  )
}
