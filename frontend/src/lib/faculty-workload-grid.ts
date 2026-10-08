export type WorkloadGridSlot = { id: string; is_break?: boolean };
export type WorkloadGridEntry = { start_slot: string; block_length?: number; period_count?: number };

export function getEntryPeriodCount(entry: WorkloadGridEntry): number {
  const count = entry.period_count ?? entry.block_length ?? 1;
  return Number.isFinite(count) && count > 0 ? Math.floor(count) : 1;
}

/** Keep a visual block inside teaching slots and stop before another class starts. */
export function getWorkloadCellSpan(
  slots: WorkloadGridSlot[],
  startIndex: number,
  requestedSpan: number,
  startsBySlot: Map<string, WorkloadGridEntry[]>,
): number {
  if (startIndex < 0 || startIndex >= slots.length || slots[startIndex].is_break) return 1;

  let span = 1;
  for (let index = startIndex + 1; index < slots.length && span < requestedSpan; index += 1) {
    const slot = slots[index];
    if (slot.is_break || (startsBySlot.get(slot.id)?.length ?? 0) > 0) break;
    span += 1;
  }
  return span;
}
