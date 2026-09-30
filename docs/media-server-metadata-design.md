# Media-server metadata: Baihe and Jellyfin/Plex (Step 116 design note)

Roadmap item 116 (`docs/baihe-roadmap-master.md`): "Plex/Jellyfin
metadata-provider adjacency for 'Fetch & add to library'". Design only;
nothing here is built. Written 2026-09-30 against `baihe-subtitler` at
1eb36e4 (Step 39's Jellyfin connector, #474). Facts about Jellyfin and Plex
come from their public behaviour as generally documented, not from a test
against a real server here; re-check them before building.

## 1. What Baihe knows about a title

| Field | Where it lives today |
|---|---|
| Title | `dramas.title_en`, and the original title in `dramas.title_zh` |
| Alternate titles | No field. Only the two titles above. (`aliases` exists for characters, glossary and wiki entries, not for a title.) |
| Cast and crew | `dramas.voice_actors` (comma-separated), `director`, `studio`, `author`, plus the `*_romanized` forms. Per drama, `characters` rows carry `character_name`, an optional `voice_actor` and `pronouns`, linked to the series-wide `series_characters` (name, aliases, gender) by `series_character_id`. So actor-to-character roles exist wherever the voice actor has been filled in. |
| Episode list | Dramas that share `series_id`, ordered by `episode_number` (Step 74). A series has a name (`series.name`) but no summary. |
| Cover | `dramas.cover_art_filename`, a file in the drama folder |
| Summary | `dramas.summary` (and `episode_summary`, an auto-generated recap used as translation context) |
| Other | `genre`, `custom_tags`, `publication_status`, `source_language`, `media_type` |

Private fields that must never leave Baihe this way: `personal_notes`,
`source_url` (it can carry a session or token in its query),
`last_translate_errors`, and any file path.

"Fetch & add to library" (Discover, `title_library.import_title_from_url`)
reads a public listing page, asks an LLM for the title metadata and adds it
to Discover's `known_titles` catalog. The drama fields above are filled by
hand or by the similar auto-fill (`services/metadata_service.py`). Neither
touches a media server today.

## 2. What the media servers read

- **Jellyfin** reads Kodi-style NFO files when its NFO metadata reader is on
  for the library (the usual default): `tvshow.nfo` in a series folder,
  `<episode stem>.nfo` next to each episode, `movie.nfo` or
  `<movie stem>.nfo` for a film, and artwork such as `poster.jpg` or
  `folder.jpg` beside them. Remote metadata comes from plugins (.NET
  assemblies installed from a plugin repository). Its HTTP API can also
  overwrite an item's metadata and images with an admin key.
- **Plex** reads local artwork (`poster.jpg` and similar) through its local
  media assets agent. It does not read NFO files without a third-party
  agent. Its newer custom metadata providers are HTTP services that the Plex
  server calls to match and describe items; `plex-anime-metadata-provider`
  is one of these, backed by AniDB and MyAnimeList.

## 3. Options

**A. NFO and poster sidecar files, written next to exported media.**
When Step 39's "Send to Jellyfin" creates a new title folder
(`<library>/<Title>/`, one folder per drama), it could also write
`movie.nfo` and copy the cover as `poster.<stored ext>` (covers are
`cover.png`, `.jpg` or `.webp`; read through `cover_art_service.cover_file`,
which only accepts that name inside the drama's own folder). Jellyfin then
shows Baihe's title, original title, summary, cast (with character roles
where `voice_actor` is set), genre and tags with no plugin and no network
call. Plex gets the poster only.
- Series are **not** covered by this first version: the send never reads
  `series_id` or `episode_number` and makes one folder per drama, so three
  episodes would become three one-episode titles. A series needs a new
  `<Series name>/Season 01/<episode>` layout for the send, then
  `tvshow.nfo` (from `series.name`) plus one `<episode>.nfo` per episode.
  That is a separate, larger change.
- Fits Step 39's rule "API and filesystem only, never a plugin".
- One-way, point-in-time copy: an edit in Baihe needs another send.
- Security: the same write guards as the subtitle send (only inside the
  configured library folder, resolved-path check, temp file and rename, no
  overwrite unless asked). Build the XML with a real XML library, never
  string templates, so a title containing `<` or `&` can't break or inject
  into the file. Write only the whitelisted fields in section 1. The route
  stays `local_only()` (it is the existing send), so no new row in
  `docs/remote-access-decision.md` beyond noting the extra files.

**B. Pull Jellyfin's metadata into Baihe (the other direction).**
The connector's read-only scan already lists Jellyfin items. For an item
Jellyfin has already matched (TMDB, AniDB and so on), Baihe could offer its
title, original title, overview, people and poster as a *suggestion* for the
drama (or a new `known_titles` entry), applied only when the user accepts,
the same suggest-then-apply pattern as
`metadata_service.autofill_suggestion`/`apply_autofill`. This is the part
that is actually "adjacent" to Fetch & add: metadata from a source already
on the LAN, without a page fetch or an LLM call.
- Security: reuses the connector's guarded client (timeouts, no redirects,
  response cap, fixed error text, key only in a header). Everything
  Jellyfin returns is untrusted text: length-cap it, validate the image type
  and size before saving a cover, and never follow an image URL outside the
  configured server. Stays `local_only()` like the rest of `/api/jellyfin/*`.

**C. Push metadata with Jellyfin's API.**
Update the item's fields and image through the connector's key. It writes
into another server's database (a "lock" flag is needed or the next library
refresh may undo it), where today the connector only reads and asks for a
refresh. More reach than A for the same result. Not recommended.

**D. A Jellyfin plugin.** A .NET provider that calls Baihe. Rejected by Step
39 in the roadmap ("never a Jellyfin plugin for v1") and still not worth it: a second
language and packaging to maintain, and it needs a Baihe endpoint (option E)
anyway.

**E. A read-only metadata endpoint (Baihe as a provider).**
Baihe serves title, people, episodes and cover to a media server, for
example in Plex's custom-provider protocol. This is what
`plex-anime-metadata-provider` does, and it is the largest option: a
show/season/episode model Baihe doesn't have (episodes are loose dramas
sharing `series_id`, and a series has a name but no summary or seasons),
matching logic
for Plex's requests, and a new kind of caller.
- Security against `docs/remote-access-decision.md`: the caller is a server,
  not a signed-in household member, so none of today's permissions fit. It
  would need its own permission (off by default) and a per-server token
  (hashed, shown once, revocable, like the planned extension tokens), must
  never be reachable through Caddy from outside the home, and would expose
  every title in the library to anything holding the token. Every field
  would go through the section 1 whitelist, and cover images would be served
  by drama id, never by a path from the request. It also lands on top of
  auth work (steps 133-140) that isn't finished.

## 4. Recommendation

1. **Build A first, as an opt-in toggle on the existing Send to Jellyfin**
   ("Also write title info and poster"), for single dramas in the
   new-title-folder layout only (series need the new folder layout above); next to an existing Jellyfin item it would fight the metadata
   Jellyfin already has. Small, local, no new route, and it is the most
   useful piece for Jellyfin users.
2. **B second, if wanted**: a "Use Jellyfin's details" suggestion on a drama
   matched in a scan. Suggestion only, never auto-applied.
3. **Don't build C, D or E now.** E is the only way to feed Plex more than a
   poster, but it needs a series model Baihe lacks and a server-to-server
   auth path; revisit only if the user asks for Plex specifically, and only
   after the remote-access auth steps are done.

A would be the first thing Baihe writes besides subtitles and video into the
library folder, so it should keep Step 39's "never overwrite unless asked"
rule for the `.nfo` and poster files as well.

Decided by the user (2026-09-30): option A lists people as **character
roles where known, plus the remaining names as plain actors**. Each drama's
`characters` rows with a `voice_actor` become `<actor>` entries with a
`<role>`; any other name in `dramas.voice_actors` becomes an `<actor>` with
no role, so nobody already credited is dropped. A name listed both ways
appears once, as the role: compare after trimming, and also against
`voice_actors_romanized`, so the same person isn't listed twice in two
spellings. That de-duplication gets its own test when A is built.
