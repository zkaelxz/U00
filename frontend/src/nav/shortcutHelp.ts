/** Opens the keyboard shortcuts sheet from outside it: the You menu's row asks GlobalShortcuts by event, as the "?" key does inside it. */
export const SHORTCUT_HELP_EVENT = 'baihe:open-keys'

export function openShortcutHelp(): void {
  window.dispatchEvent(new Event(SHORTCUT_HELP_EVENT))
}
