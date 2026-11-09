// Runs inside the comic page's own JavaScript world, so a hostile page could read the
// per-grab secret __NONCE__. It only limits feeding the bridge to the time a grab runs;
// the app takes the page address from the web view, never from this script.
(async function () {
  var nonce = "__NONCE__";
  var MIN_SIDE = 200;
  var seen = {};
  // Readers draw pages only as they scroll into view, so walk down the page first; the
  // step and the count are capped so an endless feed cannot hold the grab open.
  var lastHeight = -1;
  for (var step = 0; step < 60; step++) {
    window.scrollBy(0, Math.max(400, window.innerHeight * 0.9));
    await new Promise(function (r) { setTimeout(r, 250); });
    var atEnd = window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 4;
    if (atEnd && document.documentElement.scrollHeight === lastHeight) break;
    lastHeight = document.documentElement.scrollHeight;
  }
  window.scrollTo(0, 0);
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
  var imgs = Array.prototype.slice.call(document.images).filter(function (img) {
    return img.naturalWidth >= MIN_SIDE && img.naturalHeight >= MIN_SIDE;
  });
  function first(selector, attr) {
    var el = document.querySelector(selector);
    return el ? (attr ? el.getAttribute(attr) : el.textContent) || "" : "";
  }
  var titles = [document.title, first('meta[property="og:title"]', "content"), first("h1")]
    .map(function (t) { return t.replace(/\s+/g, " ").trim(); })
    .filter(function (t) { return t; });
  Baihe.begin(nonce, JSON.stringify({ titles: titles, count: imgs.length, chapters: chapterLinks(), next: nextChapter() }));

  function toBase64(blob) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(String(reader.result).split(",")[1] || ""); };
      reader.onerror = reject;
      reader.readAsDataURL(blob);
    });
  }
  async function bytesOf(img) {
    var src = img.currentSrc || img.src;
    try {
      var response = await fetch(src, { credentials: "include" });
      if (response.ok) {
        var blob = await response.blob();
        return { type: blob.type || "image/jpeg", data: await toBase64(blob) };
      }
    } catch (e) { /* cross-origin or blocked: fall back to the pixels already on screen */ }
    var canvas = document.createElement("canvas");
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    canvas.getContext("2d").drawImage(img, 0, 0);
    var url = canvas.toDataURL("image/png");
    return { type: "image/png", data: url.split(",")[1] || "" };
  }

  for (var i = 0; i < imgs.length; i++) {
    var img = imgs[i];
    var src = img.currentSrc || img.src;
    if (seen[src]) continue;
    seen[src] = true;
    try {
      var got = await bytesOf(img);
      Baihe.image(nonce, "p" + i, src.indexOf("http") === 0 ? src : "", got.type, got.data);
    } catch (e) {
      Baihe.skipped(nonce, "p" + i);
    }
  }
  Baihe.done(nonce);
})();
