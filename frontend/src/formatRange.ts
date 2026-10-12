/** "1-60 lines": min-max with the same decimals, the unit after. Used by numeric field hints. */
export function formatRange(min: number, max: number, unit = '', decimals = 0): string {
  const range = `${min.toFixed(decimals)}-${max.toFixed(decimals)}`
  return unit ? `${range} ${unit}` : range
}
