// The ref callback that puts a translation box's caret at its end when the box opens.

// autoFocus puts the caret at 0; editing a translation usually means adding to or fixing its end.
// Only when the box opens: on every focus it would undo a click placed mid-text or a select-all.
export const caretToEnd = (el: HTMLTextAreaElement | null) => {
  if (!el) return
  const { length } = el.value
  el.setSelectionRange(length, length)
}
