// Pure selection helpers for the Glossary panel's bulk delete.

export function toggleId(selected: ReadonlySet<number>, id: number): Set<number> {
  const next = new Set(selected)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  return next
}

/** Select all visible ids, or clear them when every one is already selected. */
export function toggleAll(selected: ReadonlySet<number>, ids: number[]): Set<number> {
  const allOn = ids.length > 0 && ids.every((id) => selected.has(id))
  return allOn ? new Set() : new Set(ids)
}

/** Drop ids that no longer exist (after a reload or a partial delete). */
export function pruneSelection(selected: ReadonlySet<number>, ids: number[]): Set<number> {
  const present = new Set(ids)
  return new Set([...selected].filter((id) => present.has(id)))
}

/** Ids to delete, in list order so the requests follow the table. */
export function selectedInOrder(selected: ReadonlySet<number>, ids: number[]): number[] {
  return ids.filter((id) => selected.has(id))
}
