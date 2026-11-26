/* The lines' text size in TV mode, kept apart from the normal Live size because a room's distance needs larger steps. */
export type TvTextSize = 's' | 'm' | 'l' | 'xl'

export const TV_SIZES: { value: TvTextSize; label: string; name: string }[] = [
  { value: 's', label: 'S', name: 'Small' },
  { value: 'm', label: 'M', name: 'Medium' },
  { value: 'l', label: 'L', name: 'Large' },
  { value: 'xl', label: 'XL', name: 'Extra large' },
]

/** How much of the width the picture takes when it sits beside the lines (a wide screen or a phone turned sideways). */
export type TvSplit = 'wide' | 'even' | 'narrow'

export const TV_SPLITS: { value: TvSplit; label: string }[] = [
  { value: 'wide', label: 'Big picture' },
  { value: 'even', label: 'Half' },
  { value: 'narrow', label: 'Small picture' },
]
