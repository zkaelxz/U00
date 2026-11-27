"""Finds the self-contained HTML pages the owner drops in the data folder's private-pages folder."""

import os
import re

import portable
from lib.errors import NotFoundError

FOLDER_NAME = "private-pages"
# One path segment of plain characters: a name that can't hold a separator, a
# drive, a dot-dot or a stream suffix never needs resolving to be safe.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}\.html")
# CON.html and friends name devices on Windows, which isfile() may not answer quickly.
_RESERVED = re.compile(r"(con|prn|aux|nul|com[0-9]|lpt[0-9])\.html", re.IGNORECASE)
_NOT_FOUND = "Not found."


def _folder() -> str:
    return os.path.join(portable.data_dir(), FOLDER_NAME)


def page_path(name: str) -> str:
    """The file for `name`, or NotFoundError for a bad name, a missing file or
    anything that is not a plain file directly inside the folder (a link out
    of it included)."""
    if not _NAME.fullmatch(name or "") or _RESERVED.fullmatch(name):
        raise NotFoundError(_NOT_FOUND)
    folder = os.path.realpath(_folder())
    path = os.path.join(folder, name)
    if os.path.islink(path) or not os.path.isfile(path) or os.path.realpath(path) != path:
        raise NotFoundError(_NOT_FOUND)
    return path


MAX_PAGE_BYTES = 64 * 1024 * 1024
# A page here runs in an opaque origin (see security_headers.PRIVATE_PAGE_CSP),
# where localStorage throws, and allowing the app's origin instead would hand
# the page the owner's sign-in. This stand-in keeps a page's saved marks in
# window.name, which survives a reload of the same tab and reaches nothing else.
_STORAGE_SHIM = (
    b"<script>(function(){var s={};try{s=JSON.parse(window.name||'{}')}catch(e){}"
    b"if(!s||typeof s!=='object'||Array.isArray(s))s={};"
    b"function w(){try{window.name=JSON.stringify(s)}catch(e){}}"
    b"var st={getItem:function(k){return Object.prototype.hasOwnProperty.call(s,k)?String(s[k]):null},"
    b"setItem:function(k,v){s[k]=String(v);w()},removeItem:function(k){delete s[k];w()},"
    b"clear:function(){s={};w()},key:function(i){return Object.keys(s)[i]===undefined?null:Object.keys(s)[i]}};"
    b"Object.defineProperty(st,'length',{get:function(){return Object.keys(s).length}});"
    b"try{Object.defineProperty(window,'localStorage',{value:st,configurable:true})}catch(e){}})()</script>"
)
_INSERT_AFTER = tuple(re.compile(rx, re.IGNORECASE)
                      for rx in (rb"<head(?:\s[^>]*)?>", rb"<html(?:\s[^>]*)?>", rb"<!doctype[^>]*>"))


def page_bytes(name: str) -> bytes:
    path = page_path(name)
    if os.path.getsize(path) > MAX_PAGE_BYTES:
        raise NotFoundError(_NOT_FOUND)
    with open(path, "rb") as f:
        body = f.read()
    # Placed right after the first opening tag so the shim runs before the
    # page's own scripts without putting content ahead of the doctype.
    at = 0
    for rx in _INSERT_AFTER:
        match = rx.search(body[:4096])
        if match:
            at = match.end()
            break
    return body[:at] + _STORAGE_SHIM + body[at:]
