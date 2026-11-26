/* The lines' text size in TV mode, kept apart from the normal Live size because a room's distance needs larger steps. */
export type TvTextSize = 's' | 'm' | 'l' | 'xl'

export const TV_SIZES: { value: TvTextSize; label: string; name: string }[] = [
  { value: 's', label: 'S', name: 'Small' },
  { value: 'm', label: 'M', name: 'Medium' },
  { value: 'l', label: 'L', name: 'Large' },
  { value: 'xl', label: 'XL', name: 'Extra large' },
]
