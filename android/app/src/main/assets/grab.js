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
      var overflow = getComputedStyle(el).overflowY;
      // A reader on a custom scrollbar (Bilibili Manga) hides the native one with overflow:hidden
      // but still scrolls by scrollTop; only a box about as tall as the screen counts.
      var byScript = overflow === "hidden" && el.clientHeight >= window.innerHeight * 0.5;
      if (room > gap + 50 && el.clientHeight > 200 && (/(auto|scroll)/.test(overflow) || byScript)) {
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
  // Elements already taken, so collecting after every scroll step does not repeat a picture.
  var known = new Set();
  // img, canvas and amp-img: some readers (baozimh mirrors) use amp-img elements, whose address
  // is in src and which hold no img until the AMP runtime loads.
  // Pictures in open shadow roots and same-site frames, after the page's own: some readers draw
  // there, where a plain querySelectorAll on the page does not reach.
  function allMedia() {
    var out = Array.prototype.slice.call(document.querySelectorAll("img, canvas, amp-img"));
    var roots = [document], seenRoots = 0;
    while (roots.length && seenRoots < 40) {
      var root = roots.shift();
      seenRoots++;
      var all = root.querySelectorAll("*");
      for (var n = 0; n < all.length && n < 6000; n++) {
        var el = all[n];
        if (el.shadowRoot) {
          roots.push(el.shadowRoot);
          Array.prototype.push.apply(out, el.shadowRoot.querySelectorAll("img, canvas"));
        } else if (el.tagName === "IFRAME") {
          try {
            if (el.contentDocument) {
              roots.push(el.contentDocument);
              Array.prototype.push.apply(out, el.contentDocument.querySelectorAll("img, canvas"));
            }
          } catch (e) { /* another site's frame */ }
        }
      }
    }
    return out;
  }
  // Readers that paint each page as a CSS background: only looked for while little was found.
  function backgrounds() {
    var all = document.querySelectorAll("div, section, li, figure, span");
    for (var n = 0; n < all.length && n < 4000; n++) {
      var el = all[n];
      if (known.has(el) || el.offsetWidth < 300 || el.offsetHeight < 300) continue;
      var m = /url\(["']?(https?:[^"')]+)["']?\)/.exec(getComputedStyle(el).backgroundImage || "");
      if (m && !seenUrls[m[1]]) {
        seenUrls[m[1]] = true;
        known.add(el);
        items.push({ kind: "url", url: m[1] });
      }
    }
  }
  function collect() {
  Array.prototype.slice.call(allMedia()).forEach(function (el) {
    if (known.has(el)) return;
    if (el.tagName === "AMP-IMG") {
      var ampUrl = lazyAddress(el) || (function () {
        try { var u = new URL(el.getAttribute("src") || "", location.href); return /^https?:$/.test(u.protocol) ? u.href : ""; } catch (e) { return ""; }
      })();
      if (ampUrl && !seenUrls[ampUrl] && !el.querySelector("img")) { seenUrls[ampUrl] = true; items.push({ kind: "url", url: ampUrl }); known.add(el); }
    } else if (el.tagName === "CANVAS") {
      if (el.width >= MIN_SIDE && el.height >= MIN_SIDE) { items.push({ kind: "canvas", el: el }); known.add(el); }
    } else if (el.naturalWidth >= MIN_SIDE && el.naturalHeight >= MIN_SIDE) {
      items.push({ kind: "img", el: el });
      known.add(el);
    } else if (el.naturalWidth < MIN_SIDE) {
      var later = lazyAddress(el);
      if (!later && !el.complete && /^https?:/.test(el.currentSrc || el.src || "") &&
          !(el.width > 0 && el.width < MIN_SIDE)) later = el.currentSrc || el.src;
      if (later && !seenUrls[later]) { seenUrls[later] = true; items.push({ kind: "url", url: later, el: el }); known.add(el); }
    }
  });
  if (items.length < 3) backgrounds();
  }
  // For readers that ignore a changed scrollTop (their own scroller listens for wheel, keys or
  // scrollIntoView): the last picture is brought into view and a wheel and key press are sent.
  function nudge(box) {
    var imgs = document.querySelectorAll("img, canvas, amp-img");
    if (imgs.length) {
      try { imgs[imgs.length - 1].scrollIntoView({ block: "end" }); } catch (e) { /* detached */ }
    }
    try { window.scrollBy(0, Math.max(400, window.innerHeight * 0.9)); } catch (e) { /* ignore */ }
    var target = document.elementFromPoint(window.innerWidth / 2, window.innerHeight / 2) || document.body;
    try {
      target.dispatchEvent(new WheelEvent("wheel", { deltaY: Math.max(400, window.innerHeight * 0.9), bubbles: true, cancelable: true }));
    } catch (e) { /* ignore */ }
    ["ArrowDown", "PageDown"].forEach(function (key) {
      try {
        target.dispatchEvent(new KeyboardEvent("keydown", { key: key, code: key, bubbles: true, cancelable: true }));
      } catch (e) { /* ignore */ }
    });
  }
  // Everything already in the page (loaded or with a lazy address) is taken before the walk, so
  // it is not lost if the page stops loading when the app is put away mid-walk.
  collect();
  var lastHeight = -1;
  var box = scrollBox();
  var stale = 0;
  var lastGrowthStep = 0;
  // A long chapter is a hundred screens, so the walk is bounded by time as well as by steps.
  var deadline = Date.now() + 6 * 60 * 1000;
  for (var step = 0; step < 800 && Date.now() < deadline; step++) {
    if (step % 10 === 0) box = scrollBox();
    var topBefore = box.scrollTop;
    // A page with CSS smooth scrolling would glide for half a second per step and look stuck
    // (scrollTop has not moved yet), so the jump is made instant.
    var nextTop = box.scrollTop + Math.max(400, box.clientHeight * 0.95);
    try { box.scrollTo({ top: nextTop, behavior: "instant" }); } catch (e) { box.scrollTop = nextTop; }
    await new Promise(function (r) { setTimeout(r, 90); });
    var room = box.scrollHeight - box.clientHeight;
    // A box that cannot be scrolled by scrollTop (or did not move) may still be driven by its
    // reader's own wheel and key handling.
    if (room < 50 || box.scrollTop === topBefore) {
      nudge(box);
      await new Promise(function (r) { setTimeout(r, 250); });
    }
    for (var waited = 0; waited < 800 && stillLoading(); waited += 100) {
      await new Promise(function (r) { setTimeout(r, 100); });
    }
    // Some readers keep only the pictures near the screen in the page, so they are taken as
    // the walk passes instead of once at the end.
    var before = items.length;
    collect();
    var grew = items.length > before;
    if (grew) lastGrowthStep = step;
    var atEnd = room >= 50 && box.scrollTop + box.clientHeight >= box.scrollHeight - 4;
    var stuck = room < 50 || (atEnd && box.scrollHeight === lastHeight);
    if (grew || !stuck) {
      stale = 0;
    } else {
      // The next pictures of a reader that adds them on reaching the end need a moment to come.
      await new Promise(function (r) { setTimeout(r, 600); });
      collect();
      if (++stale >= 4) break;
    }
    // A page whose height keeps shifting at the bottom (a footer or ad that loads late) never
    // looks "stuck" above, so reaching the end with nothing new for a while also finishes.
    if (atEnd && step - lastGrowthStep >= 8) break;
    lastHeight = box.scrollHeight;
  }
  collect();
  box.scrollTop = 0;
  var CHAPTER_TEXT = /第\s*[\d０-９一二三四五六七八九十百零]+\s*[章话話回集卷期]|(?:chapter|chap|ch|episode|ep|vol)\.?\s*[\d.]+|[\d.]+\s*[화話话章]|^\s*#?\d+\s*$/i;
  var NEXT_TEXT = /^\s*(下一章|下一话|下一話|下一回|下章|下一頁|下一页|次の話|次へ|次話|다음\s*화|다음화|next\s*(chapter|ch\.?|episode)?|next\s*›|›|»)\s*$/i;
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
  function first(selector, attr) {
    var el = document.querySelector(selector);
    return el ? (attr ? el.getAttribute(attr) : el.textContent) || "" : "";
  }
  // Reaction emotes, avatars and ads sit among the pages (a reader can end each chapter with a
  // row of emoji pictures). They are named by their own or their wrappers' class or id, or by the
  // file name, never by the series' own words (a comic can be called "Avatar"); or dropped for
  // being far smaller than the chapter's pages, square where they are tall, and repeated at one
  // size, which a unique small or square page such as a credits page is not.
  var NOISE = /emoji|emoticon|emote|reaction|avatar|gravatar|sticker|favicon|sponsor|advert/i;
  function markup(item) {
    var address = item.url || (item.el && (item.el.currentSrc || item.el.src)) || "";
    var text = address.split("?")[0].split("/").pop();
    var el = item.el;
    for (var up = 0; el && up < 4; up++, el = el.parentElement) {
      text += " " + (el.id || "") + " " + (typeof el.className === "string" ? el.className : "");
    }
    return text;
  }
  function sizeOf(item) {
    var el = item.el;
    if (!el) return null;
    var w = el.naturalWidth || el.width, h = el.naturalHeight || el.height;
    return w > 0 && h > 0 ? { w: w, h: h } : null;
  }
  function median(values) {
    return values.slice().sort(function (a, b) { return a - b; })[Math.floor(values.length / 2)];
  }
  function pagesOnly(list) {
    var sizes = list.map(sizeOf);
    var drop = list.map(function (item) { return NOISE.test(markup(item)); });
    var known = sizes.filter(Boolean);
    // Fewer than five sized pictures give no reliable idea of what a page looks like.
    if (known.length >= 5) {
      var area = median(known.map(function (s) { return s.w * s.h; }));
      var width = median(known.map(function (s) { return s.w; }));
      var ratio = median(known.map(function (s) { return s.w / s.h; }));
      var sameSize = {};
      known.forEach(function (s) { sameSize[s.w + "x" + s.h] = (sameSize[s.w + "x" + s.h] || 0) + 1; });
      sizes.forEach(function (s, i) {
        if (!s) return;
        var small = s.w * s.h < area / 4 && s.w < width * 0.6;
        var square = s.w * s.h < area / 2 && s.w / s.h > 0.8 && s.w / s.h < 1.25 && ratio < 0.9 &&
          sameSize[s.w + "x" + s.h] >= 2;
        if (small || square) drop[i] = true;
      });
    }
    // A rule that would take out half the chapter is wrong for this site, not right.
    var dropped = drop.filter(Boolean).length;
    if (dropped * 2 > list.length) return list;
    return list.filter(function (item, i) { return !drop[i]; });
  }
  items = pagesOnly(items);
  var titles = [document.title, first('meta[property="og:title"]', "content"), first("h1")]
    .map(function (t) { return t.replace(/\s+/g, " ").trim(); })
    .filter(function (t) { return t; });
  // The page's own words say what language its comic is in; the PC has no other way to know
  // for a Quick read, and a guess sends Japanese and Korean pages to the Chinese reader.
  var sample = titles.join("\n") + "\n" + ((document.body && document.body.innerText) || "").slice(0, 4000);
  // Where the chapter's number is most likely written: a reader's headings name the chapter, the
  // tab and social titles add the series and site. The PC reads the number from the first that has one.
  var chapterTitles = [first("h1"), first("h2"), first("h3"), first('meta[property="og:title"]', "content"), document.title]
    .map(function (t) { return (t || "").replace(/\s+/g, " ").trim().slice(0, 300); })
    .filter(function (t, i, all) { return t && all.indexOf(t) === i; });
  // What the page declares about the work, for the PC to fill a new title's blank author, summary
  // and cover. Novel sites often use og:novel:*; the cover is only an https address the PC checks.
  function meta(selectors) {
    for (var m = 0; m < selectors.length; m++) {
      var value = first(selectors[m], "content").replace(/\s+/g, " ").trim();
      if (value) return value;
    }
    return "";
  }
  var cover = meta(['meta[property="og:novel:cover"]', 'meta[property="og:image"]', 'meta[name="twitter:image"]']);
  Baihe.begin(nonce, JSON.stringify({ titles: titles, count: items.length, chapters: chapterLinks(), next: nextChapter(),
    lang: document.documentElement.lang || "", sample: sample, chapter_titles: chapterTitles,
    author: meta(['meta[property="og:novel:author"]', 'meta[property="book:author"]', 'meta[name="author"]']).slice(0, 200),
    summary: meta(['meta[property="og:novel:description"]', 'meta[property="og:description"]', 'meta[name="description"]']).slice(0, 2000),
    cover: /^https:\/\//.test(cover) && cover.length <= 2048 ? cover : "" }));
  // A novel chapter has no comic pictures: its prose is the container with the most text of its own
  // (text nodes and paragraphs directly inside it), so menus and comment lists around it are not taken.
  function chapterText() {
    var best = null, bestLen = 0;
    var all = document.querySelectorAll("article, main, section, div");
    for (var n = 0; n < all.length && n < 4000; n++) {
      var el = all[n], len = 0;
      for (var c = el.firstChild; c; c = c.nextSibling) {
        if (c.nodeType === 3) len += c.nodeValue.trim().length;
        else if (c.tagName === "P") len += (c.textContent || "").trim().length;
      }
      if (len > bestLen) { best = el; bestLen = len; }
    }
    if (!best || bestLen < 400) return "";
    return (best.innerText || best.textContent || "").split("\n")
      .map(function (t) { return t.trim(); })
      .filter(function (t) { return t.length > 1; }).join("\n");
  }
  if (items.length < 3) {
    var prose = chapterText();
    if (prose) {
      Baihe.text(nonce, prose);
      Baihe.done(nonce);
      return;
    }
  }

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
    // A host that never answers would otherwise hold "Reading pages" open forever; the abort
    // also covers a body that stops arriving.
    var abort = new AbortController();
    var timer = setTimeout(function () { abort.abort(); }, 30000);
    try {
      var response = await fetch(src, { credentials: "include", signal: abort.signal });
      if (response.ok) {
        var blob = await response.blob();
        return { type: blob.type || "image/jpeg", data: await toBase64(blob) };
      }
    } catch (e) { /* cross-origin, blocked or timed out: fall back to the pixels already on screen */
    } finally { clearTimeout(timer); }
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

  // Pictures of the web view's own screen, asked of the app one at a time. The app answers by
  // calling the function below, which only this grab's secret name reaches.
  var snapWaiters = {};
  var snapCount = 0;
  window["__baiheSnap" + nonce] = function (id, base64) {
    var waiter = snapWaiters[id];
    if (waiter) { delete snapWaiters[id]; waiter(base64); }
  };
  function snap() {
    return new Promise(function (resolve, reject) {
      var id = "s" + (++snapCount);
      var timer = setTimeout(function () { delete snapWaiters[id]; reject(new Error("no snapshot")); }, 15000);
      snapWaiters[id] = function (base64) {
        clearTimeout(timer);
        if (!base64) { reject(new Error("no snapshot")); return; }
        var image = new Image();
        image.onload = function () { resolve(image); };
        image.onerror = function () { reject(new Error("bad snapshot")); };
        image.src = "data:image/jpeg;base64," + base64;
      };
      Baihe.snap(nonce, id);
    });
  }
  // A canvas taller than the screen is scrolled a screen at a time; each strip is cropped from a
  // snapshot and the strips are joined. Null when the page cannot be shown whole.
  async function screenshotOf(el, box) {
    var first = el.getBoundingClientRect();
    if (first.width > window.innerWidth + 2) return null;
    var height = first.height, done = 0, out = null, startTop = box.scrollTop;
    try {
      for (var guard = 0; done < height - 1 && guard < 40; guard++) {
        var r = el.getBoundingClientRect();
        // Bring the first row not yet taken to the top of the screen; at the end of the chapter the
        // reader stops short and the row is lower down, which y below accounts for.
        box.scrollTop = Math.max(0, box.scrollTop + r.top + done);
        await new Promise(function (resolve) { setTimeout(resolve, 350); });
        r = el.getBoundingClientRect();
        var y = r.top + done;
        if (y < 0 || y >= window.innerHeight) return null;
        var rows = Math.min(height - done, window.innerHeight - y);
        var shot = await snap();
        var scale = shot.naturalWidth / window.innerWidth;
        if (!out) {
          out = document.createElement("canvas");
          out.width = Math.max(1, Math.round(r.width * scale));
          out.height = Math.max(1, Math.round(height * scale));
        }
        out.getContext("2d").drawImage(shot, Math.round(r.left * scale), Math.round(y * scale), out.width, Math.round(rows * scale),
          0, Math.round(done * scale), out.width, Math.round(rows * scale));
        done += rows;
      }
    } finally {
      box.scrollTop = startTop;
    }
    if (!out || done < height - 1) return null;
    var url = out.toDataURL("image/jpeg", 0.9);
    if (typeof url === "string" && url.indexOf("data:image") === 0) return { type: "image/jpeg", data: url.split(",")[1] || "" };
    var blob = await new Promise(function (resolve) { out.toBlob(resolve, "image/jpeg", 0.9); });
    return blob ? { type: "image/jpeg", data: await toBase64(blob) } : null;
  }

  // Pictures are read four at a time, but handed over in page order.
  var reads = {};
  function ensureRead(upTo) {
    for (var j = 0; j <= upTo && j < items.length; j++) {
      if (!reads[j] && items[j].kind === "img") reads[j] = bytesOf(items[j].el);
    }
  }
  for (var i = 0; i < items.length; i++) {
    ensureRead(i + 3);
    var item = items[i];
    try {
      if (item.kind === "url") {
        if (!seen[item.url]) { seen[item.url] = true; Baihe.pending(nonce, "p" + i, item.url); }
        continue;
      }
      if (item.kind === "canvas") {
        var shot = item.el.toDataURL("image/png");
        if (typeof shot === "string" && shot.indexOf("data:image") === 0) {
          Baihe.image(nonce, "p" + i, "", "image/png", shot.split(",")[1] || "");
          continue;
        }
        // The site disabled reading its canvas: take what the web view shows on screen instead.
        var pictured = await screenshotOf(item.el, scrollBox());
        if (pictured) Baihe.image(nonce, "p" + i, "", pictured.type, pictured.data);
        else Baihe.skipped(nonce, "p" + i);
        continue;
      }
      var img = item.el;
      var src = img.currentSrc || img.src;
      if (seen[src]) continue;
      seen[src] = true;
      var got = await reads[i];
      if (got) Baihe.image(nonce, "p" + i, src.indexOf("http") === 0 ? src : "", got.type, got.data);
      else if (src.indexOf("http") === 0) Baihe.pending(nonce, "p" + i, src);
      else Baihe.skipped(nonce, "p" + i);
    } catch (e) {
      Baihe.skipped(nonce, "p" + i);
    }
  }
  Baihe.done(nonce);
})();
