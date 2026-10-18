import { usePcPendingNote, type PcMode } from '../../hooks/usePcOnly'
import { storageSummary } from '../diskUsage/diskUsageModel'
import { PreviousLibraryCard } from '../diskUsage/PreviousLibrary'
import { TempCleanup } from '../diskUsage/TempCleanup'
import { TrashPanel } from '../diskUsage/TrashPanel'
import { UnusedVoiceClips } from '../diskUsage/UnusedVoiceClips'
import type { DiskUsage } from '../diskUsage/useDiskUsage'
import { CleanupBlock } from '../libraryAdmin/AdminBlocks'
import { TabCard } from './TabCard'

/** Free space and everything that frees it: the cleanup scan, unused voice clips, Trash, temp files and a previous library kept beside this one. PC only. */
export function StorageTab({ pc, usage }: { pc: PcMode; usage: DiskUsage }) {
  const pending = usePcPendingNote(pc)
  // Every card here reads the PC's disk on mount: nothing is asked until this is known to be the PC.
  if (pc !== 'local') return <p className="muted">{pending}</p>
  const { scan, rootBytes, trash, clips, loading, done, changed, rescan } = usage
  const summary = storageSummary(rootBytes, scan)
  return (
    <>
      <p className="du-storage-line" data-testid="storage-summary">
        {summary || (loading ? 'Measuring…' : 'Free space is not known yet.')}
      </p>
      {done && <p className="banner ok-banner du-done" role="status">{done}</p>}
      <TabCard card="cleanup"><CleanupBlock /></TabCard>
      {clips && (
        <TabCard card="unused-clips">
          <UnusedVoiceClips clips={clips} onChanged={changed} onStale={() => rescan(true)} />
        </TabCard>
      )}
      {trash && (
        <TabCard card="trash">
          <TrashPanel trash={trash} onChanged={changed} onStale={() => rescan(true)} />
        </TabCard>
      )}
      <TabCard card="temp"><TempCleanup onCleaned={() => rescan(true)} /></TabCard>
      <PreviousLibraryCard onChanged={changed} />
    </>
  )
}
