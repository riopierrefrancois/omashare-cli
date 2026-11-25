# Name, summary, chapters and transcript for every OmaShare upload

PF, 2026-11-25: "when I upload a video to OmaShare ... I'd like it to read the transcript and create one if there isn't, same as how Omaedit does it, so that it can create a relevant video name, summary, all that, maybe chapters."

Decisions PF made:
- The work happens **on the computer**, in the `omashare` CLI, not on the share site (no Deepgram/AssemblyAI/Groq keys on share.dufety.com).
- **Grok** (PF's subscription, the `grok` CLI) writes the name, summary and chapters.
- It must work for every app that calls the CLI (Omaedit, Omacut, Omareel), so it lives in the CLI.

Two repos:
- **Site** — `~/src/cap` (fork riopierrefrancois/omashare), branch `omashare/ai-content` from `omashare/share-page` (7a2da5793). Pushing `main` deploys share.dufety.com: the orchestrator does that, only with PF's go.
- **CLI** — `~/src/omashare` (riopierrefrancois/omashare-cli), branch `pf/transcript-summary` from `main` (a2dab60).

Hard limits for the implementer: do not install packages, do not push, do not touch production, do not change any file outside the ones named in a task without saying why in the report. One commit per task.

---

## Phase A — Site: accept the uploader's transcript, summary and chapters (repo `~/src/cap`)

What the share page already does (no change needed there):
- `apps/web/app/s/[videoId]/page.tsx` reads `metadata.aiTitle`, `metadata.summary`, `metadata.chapters`, `metadata.aiGenerationStatus` and shows them.
- The transcript comes from storage at `${ownerId}/${videoId}/transcription.vtt` and shows when `videos.transcriptionStatus = 'COMPLETE'`.
- Validation rules for summary and chapters exist in `apps/web/lib/ai-content.ts` (max summary length, max chapters, increasing starts, start < duration).

### Task A1 — `POST /api/desktop/video/ai-content`
In `apps/web/app/api/desktop/[...route]/video.ts`, next to `/create` and `/progress`, with the same API-key auth they use.

JSON body (zod):
```json
{
  "videoId": "string",
  "title": "string, optional, 1–200 chars",
  "summary": "string, optional",
  "chapters": [{ "title": "string", "start": 0 }],
  "vtt": "string, optional, WEBVTT text, max 2 MB"
}
```
Behaviour:
- 404 if the video does not exist; 403 if the signed-in user is not its owner.
- 400 with a plain sentence if summary/chapters fail the `ai-content.ts` validator (use the video's duration).
- If `vtt` is given and starts with `WEBVTT`: write it to `${ownerId}/${videoId}/transcription.vtt` with the same storage helper `lib/live-transcribe-runner.ts` uses to write that file (content type `text/vtt`), then set `transcriptionStatus = 'COMPLETE'`.
- Merge into `videos.metadata` (keep every other key): `aiTitle` (= title when given), `summary`, `chapters`, `aiGenerationStatus: 'COMPLETE'`. Only keys that were sent.
- If `title` is given, also set `videos.name = title`.
- Return `{ ok: true }`.
- No migration (metadata is JSON). Nothing on TiDB changes.

### Task A2 — Tests
Vitest unit tests beside the existing desktop route tests (find them with `rg -l "desktop" apps/web/__tests__`): owner check, validation error wording, metadata merge keeps other keys, VTT written to the right key and status set, title updates `name`. Mock DB/storage the way neighbouring tests do.

### Phase A gate
`cd apps/web && npx vitest run <the new test file> && npx tsc --noEmit -p .` both pass. Report the commits and the outputs' last lines.

---

## Phase B — CLI: transcript, Grok, send it (repo `~/src/omashare`)

Version becomes **0.2.0**. Python stdlib only (as today), plus subprocesses.

### New JSON events (apps depend on these names — do not rename)
Sequence for `omashare --json upload ...`:
```
{"event":"progress","stage":"preparing","percent":N}      (existing)
{"event":"progress","stage":"uploading","percent":N}      (existing)
{"event":"progress","stage":"transcribing","percent":N}   (new: only if still running after the upload)
{"event":"progress","stage":"writing","percent":N}        (new: Grok writing name/summary/chapters; 0 then 100)
{"event":"warning","message":"plain sentence"}             (new: a step was skipped; the upload still succeeds)
{"event":"done","id":"...","link":"...","title":"..."}     (existing + "title" = final name)
```
A failure in transcribing or writing is never an `error`: emit `warning` and finish with `done`. `percent` stays a decimal number.

### Task B1 — Find or make a transcript
New function `get_transcript(path, meta, workdir) -> list of cues [(start, end, text)] or None`, in this order:
1. `--transcript FILE` flag (`.vtt` or `.srt`).
2. A sidecar next to the video with the same base name: `<name>.vtt`, then `<name>.srt`.
3. Whisper, the same setup Omaedit uses:
   - Python: env `OMASHARE_WHISPER_PYTHON`, else `whisperPython` in `~/.config/omaedit/settings.json`, else the first `~/.local/share/*/venv/bin/python` that can `import faster_whisper`.
   - Model: env `OMASHARE_WHISPER_MODEL`, else `whisperModel` in that settings file, else `small.en`. Models download to faster_whisper's normal cache (Omaedit's lives under `~/.local/share/omaedit/speech/hf`; set `HF_HOME` to that folder when it exists, so the model is not downloaded twice).
   - Extract audio with ffmpeg to 16 kHz mono WAV in `workdir`, run a small inline Python script with `faster_whisper.WhisperModel(model, device="cpu", compute_type="int8")`, `vad_filter=True`, printing one JSON line per segment and a progress line (`segment.end / duration`) so the CLI can emit `transcribing` percent.
   - Run it with a memory cap: `systemd-run --user --scope -p MemoryMax=3G --quiet` when `systemd-run` exists (a heavy job once took the machine down).
4. None found and no Whisper: `warning` "No transcript: Whisper isn't set up on this computer." and skip B2/B3.
No audio stream in the file: skip silently (no warning).

### Task B2 — Run transcription during the upload
Start B1 in a thread as soon as `prepare()` is done, so it runs while the video uploads. After the upload, if it is still running, emit `transcribing` progress until it ends. Thread errors become a `warning`.

### Task B3 — Grok writes name, summary, chapters
New function `write_details(cues, duration, fallback_name) -> dict or None`.
- Run `grok -p <prompt> --disable-web-search --output-format json --json-schema <schema>` with `cwd` = an empty temp dir, timeout 180 s. Read `.text` from the JSON output and parse it as the schema object. (Verified 2026-11-25: grok 1.0.92 returns `{"text":"{...}"}`, ~12 s.)
- If the prompt is too long for the command line, check `grok help` for a stdin or file option before cutting the transcript; never silently drop the end of a transcript.
- Schema: `{title: string, summary: string, chapters: [{title: string, start: number}]}`.
- Prompt (keep the wording, fill the parts in braces):
  > You are naming a video someone is about to share with a link. Below is its transcript with start times in seconds. Video length: {duration} seconds.
  > Write: a title (under 70 characters, plain everyday words, sentence case, says what the viewer will learn or see, no emoji, no clickbait, no quotes); a summary (2 to 4 short sentences, plain words, written to the viewer, no "In this video"); chapters (only if the video is longer than 2 minutes: 3 to 8 chapters, the first starts at 0, each at least 30 seconds after the previous, starts are whole seconds taken from the transcript, short plain titles). Use the speaker's own words where you can. Shorter videos: chapters is an empty list.
  > Transcript:
  > {one line per cue: "[start] text"}
- Clean the answer before sending: drop chapters that are not increasing or start ≥ duration; first chapter forced to 0; title trimmed to 200 chars.
- `grok` missing or fails: `warning` "Couldn't write a summary: Grok didn't answer." and still send the transcript.
- Env `OMASHARE_WRITER` = `grok` (default) or `claude` (`claude -p --output-format json --model haiku ...`, read `.result`) or `off`. Keep the writer behind one small function so the other is a few lines.

### Task B4 — Send it, and pick the final name
- Build WEBVTT from the cues; `POST /api/desktop/video/ai-content` with `videoId`, `vtt`, and, when B3 worked, `title`, `summary`, `chapters`.
- Title rule: Grok's title replaces the name unless `--keep-name` is given. (Apps pass the file name as `--name` today, which is just a fallback.)
- New flags on `upload`: `--transcript FILE`, `--keep-name`, `--no-details` (skip B1–B4 entirely).
- `done` gains `"title"`. The link is copied (wl-copy) when `done` is emitted, as now.
- A failed POST is a `warning`, not an error.
- Update the module docstring's event list, `--help` texts and README (new events, flags, env vars). Bump `VERSION` and `pkg/PKGBUILD` to 0.2.0.

### Task B5 — Tests
New `tests/test_omashare.py` (stdlib `unittest`, runs with `python3 -m unittest discover tests`). Load the script with `importlib.machinery.SourceFileLoader`. Cover: VTT and SRT parsing, sidecar lookup order, VTT building, chapter cleaning, title rule with and without `--keep-name`, Grok output parsing (fake `grok` script on `PATH` that prints the verified JSON shape), Grok failure → warning + done, event order in `--json` mode against a fake local HTTP server (`http.server` in a thread) that imitates `/api/desktop/video/create`, `/api/upload/signed` (PUT target on the same fake server), the thumbnail call, and `/api/desktop/video/ai-content`. Use a 3-second ffmpeg-generated test video with a sine tone; set `OMASHARE_WHISPER_PYTHON` to a fake script that prints two canned segments.

### Phase B gate
`python3 -m unittest discover -s tests -v` passes, `python3 -m py_compile omashare` passes, `omashare --version` from the repo prints 0.2.0. Report commits and output.

---

## Phase C — Orchestrator only (not the implementer)

1. Re-run both gates myself.
2. Local end-to-end: site dev server (`~/src/cap`, tab "OmaShare dev", :3001) + `OMASHARE` CLI pointed at it with a test account; upload PF's real export `~/Videos/omareel/Recording 2026-11-24 13-44-15 - landscape.mp4` (14 min): check timings, events, memory under the cap, and the share page in the browser (title, summary, chapters, transcript).
3. With PF's go: merge `omashare/ai-content` into the fork's `main` and push (deploys), build + install `omashare` 0.2.0 pkg (PF runs `makepkg -si`), push the CLI branch.
4. Tell the Omaedit session the final event names (`transcribing`, `writing`, `warning`, `done.title`). Omacut/Omareel: note they ignore unknown stages; wording follow-up if PF wants.
5. Update the omarchy-setup README line for omashare if the install steps change.
