// help_tip.js -- opens the (i) tooltips in popup.html on hover, focus or tap and closes them with Escape or a press elsewhere.
(() => {
  const wraps = Array.from(document.querySelectorAll(".help"));
  const parts = (wrap) => ({ btn: wrap.querySelector(".help-btn"), tip: wrap.querySelector(".help-text") });

  function place(wrap) {
    const { btn, tip } = parts(wrap);
    // Fixed so the narrow popup never clips it; clamped so it stays on screen.
    tip.style.left = "0px"; tip.style.top = "0px";
    const at = btn.getBoundingClientRect();
    const box = tip.getBoundingClientRect();
    const view = document.documentElement.clientWidth;
    const left = Math.min(Math.max(8, at.right - box.width), Math.max(8, view - box.width - 8));
    const below = at.bottom + 4;
    const top = below + box.height > window.innerHeight - 8 && at.top - 4 - box.height > 8 ? at.top - 4 - box.height : below;
    tip.style.left = `${left}px`; tip.style.top = `${top}px`;
  }
  function setOpen(wrap, open) {
    const { btn, tip } = parts(wrap);
    tip.hidden = !open;
    btn.setAttribute("aria-expanded", String(open));
    if (open) place(wrap);
  }
  const isOpen = (wrap) => !parts(wrap).tip.hidden;

  for (const wrap of wraps) {
    const { btn } = parts(wrap);
    wrap.addEventListener("pointerenter", (e) => { if (e.pointerType !== "touch") setOpen(wrap, true); });
    wrap.addEventListener("pointerleave", (e) => { if (e.pointerType !== "touch" && document.activeElement !== btn) setOpen(wrap, false); });
    btn.addEventListener("focus", () => setOpen(wrap, true));
    btn.addEventListener("blur", () => setOpen(wrap, false));
    // A tap focuses the button, which already opened the tip, so only a tap on an open tip from touch closes it.
    btn.addEventListener("click", (e) => { if (e.detail > 0 && e.pointerType === "touch" && isOpen(wrap)) { setOpen(wrap, false); btn.blur(); } });
    btn.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); setOpen(wrap, false); } });
  }
  document.addEventListener("pointerdown", (e) => {
    for (const wrap of wraps) if (isOpen(wrap) && !wrap.contains(e.target)) setOpen(wrap, false);
  });
})();
