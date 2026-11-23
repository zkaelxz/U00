// Feather-style 24px paths, keyed by registry label. Labels stay the accessible names when the rail is collapsed.
const ICONS: Record<string, string> = {
  Library: 'm16 6 4 14M12 6v14M8 8v12M4 4v16',
  'Library tools': 'M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.5-.5-.5-2.5z',
  Find: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18',
  Add: 'M5 5h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2zM7 15h4M15 15h2M7 11h2M13 11h4',
  'Text': 'M4 5h8M8 3v2M5 9c1 3 4 6 7 7M11 5c0 5-3 9-7 11M13 21l4-10 4 10M14.5 18h5',
  Jobs: 'm3 17 2 2 4-4M3 7l2 2 4-4M13 6h8M13 12h8M13 18h8',
  Live: 'M2 10v3M6 6v11M10 3v18M14 8v7M18 5v13M22 10v3',
  Settings: 'M4 6h10M18 6h2M4 12h2M10 12h10M4 18h12M20 18h0M16 4v4M8 10v4M18 16v4',
  'People and access': 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75',
  Diagnostics: 'M3 12h4l3-8 4 16 3-8h4',
  'Benchmark Lab': 'M10 3v6L4 19a2 2 0 0 0 2 3h12a2 2 0 0 0 2-3l-6-10V3M9 3h6',
  Assistant: 'M4 6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H9l-5 4z',
}

/** The path for a registry label's icon (Library's for an unknown one). */
export const navIconPath = (label: string): string => ICONS[label] ?? ICONS.Library

