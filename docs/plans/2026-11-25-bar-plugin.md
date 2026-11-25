# OmaShare in the top bar

PF, 2026-11-25: "create a plugin for OmaShare so that I can see it in my top bar." PF picked all four:
1. **Shares in progress** — while any app (Omaedit, Omacut, Omareel, the command line) shares a video, the bar icon shows it.
2. **Recent shares** — click the icon: latest videos with thumbnail, title, date, Copy link, Open, and a way to My videos.
3. **Share a video from the bar** — pick a file, share it (same transcript/name/summary/chapters as any share).
4. **New comments and views** — a dot on the icon when someone comments, replies, reacts or watches; counts in the list.

Rules from PF that apply: plain everyday words in the UI (no tech words), "Workhive" spelling, controls react instantly.

Three repos, one phase each. The JSON formats below are the contract between them, so the phases can run in parallel.
- **S — Site:** `~/src/cap`, branch `omashare/bar-activity` (from main f73643dea). Push = deploy; orchestrator only, with PF's go.
- **C — Uploader:** `~/src/omashare`, branch `pf/bar-plugin` (from main e8e17c7). Version 0.3.0.
- **P — Plugin:** `~/src/omarchy-omashare-bar` (new local repo, branch `main`), Omarchy shell plugin id `omashare.share`.

Hard limits for implementers: no package installs, no pushes, nothing against share.dufety.com or production, don't touch files outside the task without saying why, one commit per task. In `~/src/cap` follow its AGENTS.md (no co-author trailers there).

---

## Contract

### C ↔ P: job files (shares in progress)
Every `omashare upload` (JSON mode or not) writes `$XDG_RUNTIME_DIR/omashare/jobs/<pid>.json` (fallback `/tmp/omashare-$UID/jobs/`), replaced atomically (write temp file in the same dir, `os.replace`) on each change, at most 4 times a second:
```json
{"pid": 1234, "file": "/abs/path.mp4", "name": "fallback or final title",
 "state": "running" | "done" | "failed",
 "stage": "preparing" | "uploading" | "transcribing" | "writing",
 "percent": 42.5, "startedAt": 1764000000.0, "updatedAt": 1764000005.2,
 "link": "https://…", "title": "…", "warning": "…", "error": "…"}
```
`link`/`title` only when done, `error` only when failed, `warning` when any. On exit (success, error, Ctrl-C) the file is left in `done`/`failed` state. `omashare` deletes job files older than 10 minutes in `done`/`failed` state, and `running` ones whose pid is gone, at every start.

### C ↔ P: `omashare recent --json [--limit N]` (default 10, max 30)
Prints one JSON object:
```json
{"server": "https://share.dufety.com", "dashboard": "https://share.dufety.com/dashboard/caps",
 "unread": 3,
 "videos": [{"id": "…", "title": "…", "createdAt": "ISO-8601", "duration": 155.2,
             "link": "https://…/s/…", "thumbnail": "/home/pfr/.cache/omashare/thumbs/<id>.jpg or null",
             "views": 4, "comments": 1, "reactions": 2, "unread": 1}]}
```
Errors: `{"error": "plain sentence"}` and exit 1 (not signed in: "Sign in to OmaShare first: run omashare login in a terminal.").

### C ↔ P: `omashare activity --json [--limit N]` and `omashare activity --read (--all | ID...)`
```json
{"unread": 3, "items": [{"id": "…", "kind": "comment" | "reply" | "reaction" | "view",
  "who": "Sam" | null, "text": "comment text (≤ 200 chars)" | null,
  "videoId": "…", "videoTitle": "…", "link": "https://…/s/…", "createdAt": "ISO-8601", "read": false}]}
```
`anon_view` is reported as kind `view` with `who: null`. `--read` prints `{"ok": true}`.

### S ↔ C: site endpoints (desktop API key auth, same middleware as `/api/desktop/video/create`)
- `GET /api/desktop/video/recent?limit=N` → `{"unread": n, "videos": [{id, title, createdAt, duration, views, comments, reactions, unread, thumbnailUrl}]}` for the signed-in user's own videos, newest first. `comments` = comments of type text, `reactions` = type emoji. `views` = number of `view` + `anon_view` notifications for that video (the site has no Tinybird, so this is the count it has). `unread` per video = unread notifications for it. `thumbnailUrl` = a signed GET URL (1 hour) for `<owner>/<video>/screenshot/screen-capture.jpg` when that object exists, else null (check how the dashboard builds its thumbnail URL and reuse that).
- `GET /api/desktop/notifications?limit=N` → `{"unread": n, "items": [{id, type, videoId, videoTitle, authorName, guestName, anonName, comment: {content} | null, createdAt, readAt}]}`; only the signed-in user's notifications (recipientId), newest first, limit ≤ 50. Author name from users table when `authorId` set.
- `POST /api/desktop/notifications/read` body `{"all": true}` or `{"ids": ["…"]}` → `{"ok": true}`; only marks the user's own rows.

---

## Phase S — Site (`~/src/cap`)

### Task S1 — the three endpoints above
In `apps/web/app/api/desktop/[...route]/` (new file `activity.ts` wired in `route.ts` the way `video.ts` is, or added to `video.ts` if routing makes that simpler — say which). zod-validate inputs; cap limits; no data from other users ever.

### Task S2 — tests
Vitest unit tests next to `apps/web/__tests__/unit/desktop-video-ai-content.test.ts`, same mocking style: own-videos only, counts by type, unread counts, notifications limited to recipient, read marks only own rows, limit bounds.

### Phase S gate
`cd apps/web && npx vitest run <new test files> __tests__/unit/desktop-video-ai-content.test.ts && npx tsc --noEmit -p .` pass.

---

## Phase C — Uploader (`~/src/omashare`)

### Task C1 — job files (contract above), written from `cmd_upload` via the existing `progress()`/`warn()` paths and at done/error. Never let a job-file write failure break an upload (catch OSError).

### Task C2 — `omashare recent` and `omashare activity` (contract above)
- Thumbnails: download `thumbnailUrl` to `~/.cache/omashare/thumbs/<id>.jpg` only when the file is missing; max 2 MB; keep it only if it starts with JPEG (`FF D8 FF`) or PNG magic; timeout 10 s; failures → `thumbnail: null`. Video ids must match `^[A-Za-z0-9_-]{1,64}$` before being used in a path.
- `link` = `<server>/s/<id>`.
- Trim every string the plugin will show (titles 200, text 200, names 80).
- Also add `omashare share-file`? No — the plugin calls `omashare --json upload FILE` directly.

### Task C3 — tests + docs
Extend `tests/test_omashare.py` with the fake server: job file lifecycle (running → done with link/title; failed with error; stale cleanup), recent JSON shape, thumbnail magic/size checks and bad id rejected, activity read/unread calls, not-signed-in error. README: new commands and the job files. Version 0.3.0 in `omashare` and `pkg/PKGBUILD`.

### Phase C gate
`python3 -m unittest discover -s tests` passes; `python3 -m py_compile omashare`; `./omashare --version` → 0.3.0.

---

## Phase P — Plugin (`~/src/omarchy-omashare-bar`)

Read first: `~/.claude/skills/omarchy/plugins.md` (plugin safety, motion, translations), and copy the structure of PF's Omareel plugin `~/.config/omarchy/plugins/omareel-recorder/` (manifest.json, Panel.qml built on `Panel` + `BarIconButton` + `KeyboardPanel`, a `tools.sh` helper, README, LICENSE). Built-in plugins under `/usr/share/omarchy/shell/plugins/` are references (read only).

Files: `manifest.json` (id `omashare.share`, name "OmaShare", version 1.0.0, kinds bar-widget, category "Capture" or "Sharing", defaultSection `right`), `Panel.qml`, `tools.sh`, `i18n/en.json`, `README.md`, `LICENSE` (already there).

### Task P1 — bar icon
- Idle: a share glyph (use a Nerd Font glyph the way other bar widgets do; check what the font provides), in the bar's foreground colour.
- Unread activity: a small dot on the glyph (theme accent/urgent colour), count in the tooltip ("3 new: 2 comments, 1 view").
- Sharing (any job `running`): a thin progress ring around the glyph filled to `percent` (uploading) or a slow spinner (transcribing/writing); tooltip shows the stage in plain words.
- Just finished: a check mark for 4 s and the shell's notification "Link copied" with the title (failed: "Couldn't share <file name>" with the error). Only for jobs that finished while the shell was running (don't replay old ones after a reload).
- Left click opens the panel; middle click opens "Share a video…" directly.

### Task P2 — the panel (`KeyboardPanel`, keyboard-navigable like Omareel's)
Top to bottom:
1. **Sharing now** (only when jobs are running): one row each — file name, plain stage ("Getting the video ready", "Uploading 42%", "Listening to the video", "Writing a name and summary"), a progress bar.
2. **Share a video…** button → `omarchy file select --title "Share a video" --extensions "mp4 mov mkv webm m4v"`; when a file comes back, start `omashare --json upload <file>` with `systemd-run --user --collect --quiet` so the share keeps going if the shell reloads; progress comes back through the job files, not stdout.
3. **New** (only when unread > 0): up to 5 items in plain words — "Sam commented on <title>: “…”", "Someone watched <title>", "Sam reacted to <title>", "Sam replied on <title>"; relative time ("5 min ago"). Click opens the link and marks that item read. "Mark all as seen" link.
4. **Recent shares**: up to 8 rows — thumbnail (local file only; placeholder when null), title, "Nov 25 · 2:35", small counts "4 views · 1 comment"; buttons Copy link (wl-copy, then "Copied" for 2 s) and Open (xdg-open). Click on the row = Open.
5. Footer: "My videos" → opens `dashboard` URL.
- Empty state: "No shares yet. Share a video from Omaedit, Omacut, Omareel, or here."
- Not signed in: "Sign in to OmaShare first: run omashare login in a terminal." and nothing else.
- `omashare` not installed: the widget hides.

### Task P3 — data and timing
- Job files: poll the jobs directory every 1 s while any job is running or the panel is open, otherwise every 5 s (cheap: list + read small JSON). Ignore files that don't parse.
- `omashare recent --json` and `omashare activity --json`: on panel open (show the last result instantly, refresh in the background), after a job finishes, and every 5 minutes in the background. A setting `checkForActivity` (default true) in the plugin's settings turns the background check off; describe it in README. Never run two at once.
- Treat all of it as untrusted: every `Text` uses `textFormat: Text.PlainText`; bound list sizes; never interpolate titles or paths into a shell string (pass args as a list).

### Task P4 — README + i18n
README: what it does, install (`ln -s ~/src/omarchy-omashare-bar ~/.config/omarchy/plugins/omashare-share`, then `omarchy bar add omashare.share --section right` or whatever `omarchy bar --help` says), needs `omashare` ≥ 0.3.0. `i18n/en.json` holds every UI string; plurals for counts.

### Phase P gate
`qmllint` if available on all QML (report what's not available), `bash -n tools.sh`, manifest is valid JSON. The orchestrator then links it into the shell and checks it on screen.

---

## Phase V — Orchestrator only
1. Re-run all gates.
2. Deploy S (PF's go already covers it? No — ask PF), install C (`makepkg -si`, PF), link P into `~/.config/omarchy/plugins/`, add to the bar, screenshot: idle, panel with recent shares, a live share from the bar (a small test clip, then delete it), activity dot after a guest comment on that test clip (then delete).
3. Push the plugin repo to GitHub only with PF's OK (private or public: ask). omarchy-setup README: one line with the repo URL.
