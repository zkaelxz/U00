import { expect, test } from '@playwright/test'

import { CAND_ID, SCREENS, guard, mockVoiceClone, openVoices } from './voiceCloneMocks'

// Dub > Voices and cloning (parity blocker #7). Everything the panel calls is
// mocked (voiceCloneMocks.ts); guard() aborts and records any unmocked write.

test('clone warning, extract candidates, preview and pick one', async ({ page }) => {
  const unmocked = await guard(page)
  const m = await mockVoiceClone(page)
  const panel = await openVoices(page)

  await expect(page.getByTestId('dub-clone-warning')).toContainText('1 speaker is not set up for cloning')
  const wei = panel.getByRole('listitem', { name: 'Voice for SPEAKER_00' })
  await expect(wei.getByTestId('clone-warning')).toContainText('plain TTS voice')
  await expect(wei.getByTestId('clip-status')).toContainText('No reference clip')

  await wei.getByRole('button', { name: 'Find clips in the audio' }).click()
  await expect(panel.getByTestId('voice-extract-status')).toContainText('running')
  expect(m.posts.at(-1)?.body).toEqual({ speaker_label: 'SPEAKER_00', max_candidates: 3 })
  m.state.jobDone = true
  await expect(panel.getByTestId('voice-extract-status')).toContainText('2 candidate clip(s) ready.')

  const cands = wei.getByRole('list', { name: 'Candidate clips for SPEAKER_00' })
  await expect(cands.getByRole('listitem')).toHaveCount(2)
  await expect(cands).toContainText('6.0 s · 魏婴，你在哪里')
  await expect(cands).toContainText('no matching line')
  await expect(cands.locator('audio').first()).toHaveAttribute('src', `/api/characters/dramas/1/reference-clips/candidates/${CAND_ID}/audio`)
  const lan = panel.getByRole('listitem', { name: 'Voice for SPEAKER_01' })
  await expect(lan.getByTestId('skip-reason')).toContainText('2.1 s, under the 3 s minimum')
  await page.screenshot({ path: `${SCREENS}/desktop-candidates.png`, fullPage: true })

  await wei.getByRole('button', { name: 'Use clip 1' }).click()
  await expect(wei.getByRole('status')).toHaveText('Clip chosen.')
  await expect(wei.getByTestId('clip-status')).toContainText('Reference clip set, with transcript')
  expect(m.posts.at(-1)?.url).toContain(`/candidates/${CAND_ID}/choose`)
  expect(unmocked).toEqual([])
})

test('upload, remove with confirm, voice actor, series link and voice bank', async ({ page }) => {
  const unmocked = await guard(page)
  const m = await mockVoiceClone(page)
  const panel = await openVoices(page)
  const wei = panel.getByRole('listitem', { name: 'Voice for SPEAKER_00' })

  await expect(wei.getByRole('button', { name: 'Save to voice bank' })).toBeDisabled()
  await expect(wei).toContainText('Set a reference clip first.')

  await wei.getByLabel('Clip file for SPEAKER_00').setInputFiles({ name: 'wei.wav', mimeType: 'audio/wav', buffer: Buffer.from('RIFF') })
  await wei.getByLabel('Clip transcript for SPEAKER_00').fill('你好')
  await wei.getByRole('button', { name: 'Upload clip' }).click()
  await expect(wei.getByRole('status')).toHaveText('Clip uploaded.')
  const upload = m.posts.at(-1)!
  expect(upload.headers['x-baihe-local']).toBe('1')
  expect(String(upload.body)).toContain('name="speaker_label"')
  expect(String(upload.body)).toContain('SPEAKER_00')
  await expect(wei.getByRole('button', { name: 'Replace clip' })).toBeVisible()

  await wei.getByRole('button', { name: 'Remove clip for SPEAKER_00' }).click()
  await wei.getByRole('button', { name: 'Confirm remove clip for SPEAKER_00' }).click()
  await expect(wei.getByRole('status')).toHaveText('Clip removed.')
  expect(m.posts.at(-1)?.body).toEqual({ speaker_label: 'SPEAKER_00', confirm: true })

  await wei.getByLabel('Voice actor for SPEAKER_00').fill('路知行')
  await wei.getByRole('button', { name: 'Save actor' }).click()
  await expect(wei.getByRole('status')).toHaveText('Voice actor saved.')
  expect(m.posts.at(-1)?.body).toEqual({ speaker_label: 'SPEAKER_00', voice_actor: '路知行' })

  await wei.getByLabel('Series character for SPEAKER_00').selectOption('11')
  await expect(wei.getByRole('heading', { level: 4 })).toHaveText('Wei Wuxian (SPEAKER_00)')
  expect(m.posts.at(-1)?.body).toEqual({ speaker_label: 'SPEAKER_00', series_character_id: 11 })
  await wei.getByLabel('Series character for SPEAKER_00').selectOption('')
  await expect(wei.getByRole('status')).toHaveText('Unlinked.')
  expect(m.posts.at(-1)?.body).toEqual({ speaker_label: 'SPEAKER_00', series_character_id: null })

  // Upload again, then save to the bank.
  await wei.getByLabel('Clip file for SPEAKER_00').setInputFiles({ name: 'wei.wav', mimeType: 'audio/wav', buffer: Buffer.from('RIFF') })
  await wei.getByRole('button', { name: 'Upload clip' }).click()
  await wei.getByLabel('Voice bank name for SPEAKER_00').fill('Wei voice')
  await wei.getByRole('button', { name: 'Save to voice bank' }).click()
  await expect(wei.getByRole('status')).toHaveText('Saved "Wei voice" to the voice bank.')
  expect(m.posts.at(-1)?.body).toEqual({ speaker_label: 'SPEAKER_00', name: 'Wei voice', notes: '' })

  const lan = panel.getByRole('listitem', { name: 'Voice for SPEAKER_01' })
  await lan.getByLabel('Voice bank entry for SPEAKER_01').selectOption('3')
  await lan.getByRole('button', { name: 'Apply voice' }).click()
  await expect(lan.getByRole('status')).toHaveText('Voice applied.')
  expect(m.posts.at(-1)?.body).toEqual({ speaker_label: 'SPEAKER_01', voice_bank_id: 3 })
  await page.screenshot({ path: `${SCREENS}/desktop-after-edits.png`, fullPage: true })
  expect(unmocked).toEqual([])
})

test('remote: upload and remove are hidden with a PC-only note', async ({ page }) => {
  const unmocked = await guard(page)
  await mockVoiceClone(page, { remote: true })
  const panel = await openVoices(page)
  const wei = panel.getByRole('listitem', { name: 'Voice for SPEAKER_00' })
  await expect(wei).toContainText('Uploading or removing a clip is PC only.')
  await expect(wei.getByLabel('Clip file for SPEAKER_00')).toHaveCount(0)
  await expect(wei.getByRole('button', { name: 'Find clips in the audio' })).toBeEnabled()
  await page.screenshot({ path: `${SCREENS}/desktop-remote.png`, fullPage: true })
  expect(unmocked).toEqual([])
})
