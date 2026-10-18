import { BackupBlock, RestoreBlock, UserBackup } from '../libraryAdmin/AdminBlocks'
import { SnapshotBlock } from '../libraryAdmin/SnapshotBlock'
import { TabCard } from './TabCard'

/** "Backup and restore", in the order people use them: back up, restore, then the automatic copies. PC only. */
export function BackupTab() {
  return (
    <>
      <h3 className="tools-group-title">Backup and restore</h3>
      <TabCard card="backup">
        <BackupBlock />
        <UserBackup />
      </TabCard>
      <TabCard card="restore"><RestoreBlock /></TabCard>
      <TabCard card="auto-copies"><SnapshotBlock /></TabCard>
    </>
  )
}
