import { describe, expect, it } from 'vitest'

import { dubJobIds, isBulkJobId, isResegmentPreviewJob, resegmentJobIds, mediaExportJobId, reviewJobIds, reviewKindOf, translateJobIds, voiceCloneJobId } from './stageJobIds'

describe('stage job ids', () => {
  it('match the server-side per-drama job ids', () => {
    expect(translateJobIds(3)).toEqual(['translate_3', 'bulk_translate_3'])
    expect(dubJobIds(3)).toEqual(['dub_3', 'narration_3'])
    expect(reviewJobIds(3)).toEqual([
      'consistency_3', 'emotion_3', 'notes_3', 'flag_3', 'fixflag_3',
    ])
    expect(mediaExportJobId(3, 'audio')).toBe('audiobook_3')
    expect(mediaExportJobId(3, 'video')).toBe('burned_video_3')
    expect(mediaExportJobId(3, 'softsub_video')).toBe('softsub_video_3')
    expect(mediaExportJobId(3, 'dubbed_video')).toBe('dubbed_video_3')
    expect(voiceCloneJobId(3)).toBe('voiceref_3')
    expect(resegmentJobIds(3)).toEqual(['resegment_3', 'resegpreview_3'])
    expect(isResegmentPreviewJob('resegpreview_3')).toBe(true)
    expect(isResegmentPreviewJob('resegment_3')).toBe(false)
  })
  it('names the check a review job id was started for, so Retry calls the same start', () => {
    expect(reviewKindOf('fixflag_3')).toBe('fix-flagged')
    expect(reviewKindOf('notes_3')).toBe('notes')
    expect(reviewKindOf('bulk_flag_3')).toBeNull()
    expect(reviewKindOf('translate_3')).toBeNull()
  })
  it('tells bulk batches from normal runs', () => {
    expect(isBulkJobId('bulk_translate_3')).toBe(true)
    expect(isBulkJobId('bulk_flag_3')).toBe(true)
    expect(isBulkJobId('translate_3')).toBe(false)
    expect(isBulkJobId(null)).toBe(false)
  })
})
