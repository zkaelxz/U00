// Runs inside the comic page's own JavaScript world, so a hostile page could read the
// per-grab secret __NONCE__. It only limits feeding the bridge to the time a grab runs;
// the app takes the page address from the web view, never from this script.
(async function () {
  var nonce = "__NONCE__";
  var MIN_SIDE = 200;
  var seen = {};
  // Readers draw pages only as they scroll into view, so walk down the page first; the
  // step and the count are capped so an endless feed cannot hold the grab open.
  // Many readers scroll an inner box, not the window, so the tallest scrollable element is
  // scrolled instead of the page when one is taller than the page itself.
  function scrollBox() {
    var best = document.scrollingElement || document.documentElement;
    var gap = best.scrollHeight - best.clientHeight;
    var all = document.querySelectorAll("div, main, section, article, ul");
    for (var n = 0; n < all.length && n < 4000; n++) {
      var el = all[n];
      var room = el.scrollHeight - el.clientHeight;
      if (room > gap + 50 && el.clientHeight > 200 && /(auto|scroll)/.test(getComputedStyle(el).overflowY)) {
        best = el;
        gap = room;
      }
    }
    return best;
  }
  // Waits briefly after each step, and longer only while a picture on screen is still loading,
  // so a fast connection is not held back and a slow one is not skipped past.
  function stillLoading() {
    var list = document.images;
    for (var k = 0; k < list.length; k++) {
      var r = list[k].getBoundingClientRect();
      if (!list[k].complete && r.bottom > 0 && r.top < window.innerHeight * 2) return true;
    }
    return false;
  }
  var lastHeight = -1;
  var box = scrollBox();
  for (var step = 0; step < 150; step++) {
    if (step % 10 === 0) box = scrollBox();
    box.scrollTop += Math.max(400, box.clientHeight * 0.95);
    await new Promise(function (r) { setTimeout(r, 90); });
    for (var waited = 0; waited < 800 && stillLoading(); waited += 100) {
      await new Promise(function (r) { setTimeout(r, 100); });
    }
    var atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 4;
    if (atEnd && box.scrollHeight === lastHeight) break;
    lastHeight = box.scrollHeight;
  }
  box.scrollTop = 0;
  var CHAPTER_TEXT = /第\s*[\d０-９一二三四五六七八九十百零]+\s*[章话話回集卷期]|(?:chapter|chap|ch|episode|ep|vol)\.?\s*[\d.]+|[\d.]+\s*[화話话章]|^\s*#?\d+\s*$/i;
  var NEXT_TEXT = /^\s*(下一章|下一话|下一話|下一回|下章|次の話|次へ|次話|다음\s*화|다음화|next\s*(chapter|ch\.?|episode)?|next\s*›|›|»)\s*$/i;
  function chapterLinks() {
    var out = [], keys = {};
    var anchors = document.querySelectorAll("a[href]");
    for (var i = 0; i < anchors.length && out.length < 500; i++) {
      var a = anchors[i];
      var text = (a.textContent || "").replace(/\s+/g, " ").trim().slice(0, 120);
      if (!text || !CHAPTER_TEXT.test(text)) continue;
      var u;
      try { u = new URL(a.href); } catch (e) { continue; }
      if (u.host !== location.host || !/^https?:$/.test(u.protocol)) continue;
      var key = u.origin + u.pathname;
      if (keys[key] || key === location.origin + location.pathname) continue;
      keys[key] = true;
      out.push({ title: text, url: key });
    }
    return out;
  }
  // rel=next is the site's own statement; the link text is the fallback. Java re-checks
  // that the address is on the same site.
  function nextChapter() {
    var el = document.querySelector('a[rel~="next"][href], link[rel~="next"][href]');
    if (!el) {
      var anchors = document.querySelectorAll("a[href]");
      for (var i = 0; i < anchors.length; i++) {
        if (NEXT_TEXT.test((anchors[i].textContent || "").replace(/\s+/g, " "))) { el = anchors[i]; break; }
      }
    }
    return el ? el.href : "";
  }
  // In page order: loaded pictures, pictures a lazy loader has not filled in yet (their real
  // address is in a data attribute), and canvases a reader draws onto.
  var LAZY = ["data-src", "data-original", "data-lazy-src", "data-lazy"];
  function lazyAddress(img) {
    for (var k = 0; k < LAZY.length; k++) {
      var v = img.getAttribute(LAZY[k]);
      if (v) {
        try { var u = new URL(v, location.href); if (/^https?:$/.test(u.protocol)) return u.href; } catch (e) { /* not an address */ }
      }
    }
    return "";
  }
  var items = [];
  var seenUrls = {};
  // img, canvas and amp-img: some readers (baozimh mirrors) use amp-img elements, whose address
  // is in src and which hold no img until the AMP runtime loads.
  Array.prototype.slice.call(document.querySelectorAll("img, canvas, amp-img")).forEach(function (el) {
    if (el.tagName === "AMP-IMG") {
      var ampUrl = lazyAddress(el) || (function () {
        try { var u = new URL(el.getAttribute("src") || "", location.href); return /^https?:$/.test(u.protocol) ? u.href : ""; } catch (e) { return ""; }
      })();
      if (ampUrl && !seenUrls[ampUrl] && !el.querySelector("img")) { seenUrls[ampUrl] = true; items.push({ kind: "url", url: ampUrl }); }
    } else if (el.tagName === "CANVAS") {
      if (el.width >= MIN_SIDE && el.height >= MIN_SIDE) items.push({ kind: "canvas", el: el });
    } else if (el.naturalWidth >= MIN_SIDE && el.naturalHeight >= MIN_SIDE) {
      items.push({ kind: "img", el: el });
    } else if (el.naturalWidth < MIN_SIDE) {
      var later = lazyAddress(el);
      if (!later && !el.complete && /^https?:/.test(el.currentSrc || el.src || "") &&
          !(el.width > 0 && el.width < MIN_SIDE)) later = el.currentSrc || el.src;
      if (later) items.push({ kind: "url", url: later });
    }
  });
  function first(selector, attr) {
    var el = document.querySelector(selector);
    return el ? (attr ? el.getAttribute(attr) : el.textContent) || "" : "";
  }
  var titles = [document.title, first('meta[property="og:title"]', "content"), first("h1")]
    .map(function (t) { return t.replace(/\s+/g, " ").trim(); })
    .filter(function (t) { return t; });
  Baihe.begin(nonce, JSON.stringify({ titles: titles, count: items.length, chapters: chapterLinks(), next: nextChapter() }));

  function toBase64(blob) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(String(reader.result).split(",")[1] || ""); };
      reader.onerror = reject;
      reader.readAsDataURL(blob);
    });
  }
  // Bytes the page lets this script read, or null when the site blocks it (a picture from
  // another host without CORS headers makes the canvas unreadable); the app then downloads it
  // itself with the page's cookies.
  async function bytesOf(img) {
    var src = img.currentSrc || img.src;
    try {
      var response = await fetch(src, { credentials: "include" });
      if (response.ok) {
        var blob = await response.blob();
        return { type: blob.type || "image/jpeg", data: await toBase64(blob) };
      }
    } catch (e) { /* cross-origin or blocked: fall back to the pixels already on screen */ }
    try {
      var canvas = document.createElement("canvas");
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      canvas.getContext("2d").drawImage(img, 0, 0);
      var url = canvas.toDataURL("image/png");
      return { type: "image/png", data: url.split(",")[1] || "" };
    } catch (e) {
      return null;
    }
  }

  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    try {
      if (item.kind === "url") {
        if (!seen[item.url]) { seen[item.url] = true; Baihe.pending(nonce, "p" + i, item.url); }
        continue;
      }
      if (item.kind === "canvas") {
        var shot = item.el.toDataURL("image/png");
        Baihe.image(nonce, "p" + i, "", "image/png", shot.split(",")[1] || "");
        continue;
      }
      var img = item.el;
      var src = img.currentSrc || img.src;
      if (seen[src]) continue;
      seen[src] = true;
      var got = await bytesOf(img);
      if (got) Baihe.image(nonce, "p" + i, src.indexOf("http") === 0 ? src : "", got.type, got.data);
      else if (src.indexOf("http") === 0) Baihe.pending(nonce, "p" + i, src);
      else Baihe.skipped(nonce, "p" + i);
    } catch (e) {
      Baihe.skipped(nonce, "p" + i);
    }
  }
  Baihe.done(nonce);
})();
