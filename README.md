# OmaShare

Share a video as a link, Loom style, from your own server.

The server is [Cap Web](https://github.com/CapSoftware/Cap) (self-hosted). It gives each video a share page with comments, reactions and a transcript. The `omashare` command uploads videos to it. Omareel, Omacut and Omaedit call that command for their Share link button.

## Use it

```sh
omashare login https://share.example.com   # once; opens your browser to sign in
omashare talk.mp4                          # uploads, prints the link, copies it
omashare talk.mov --name "Weekly update"   # fallback title; AI replaces it
omashare talk.mp4 --transcript talk.srt     # use an existing transcript
omashare talk.mp4 --keep-name              # keep the file name instead of the AI title
omashare talk.mp4 --no-details             # upload only, no transcript or AI
omashare names add "Workhive"              # keep this spelling in transcript and AI
omashare names                            # list saved names
omashare names remove "Workhive"
omashare status                            # which server and account
omashare recent --json                      # latest shares, counts and local thumbnails
omashare recent --json --limit 8
omashare activity --json                    # new comments, replies, reactions and views
omashare activity --read ID                  # mark one or more notification IDs as seen
omashare activity --read --all               # mark all as seen
omashare logout
```

Any video ffmpeg can read works. MP4 files with H.264 and AAC go up as they are. Anything else (WebM, MKV, MOV, HEVC, 10-bit) is first converted to an MP4 every browser plays.

The key is saved in `~/.config/omashare/config.json` (readable only by you). `OMASHARE_SERVER` and `OMASHARE_KEY` override it.

Needs `python3` and `ffmpeg`. `wl-copy` is optional; it copies the link. When there is
no sidecar VTT/SRT transcript, transcription uses Omaedit's faster-whisper Python
and model (or `OMASHARE_WHISPER_PYTHON` and `OMASHARE_WHISPER_MODEL`; default
model `small.en`). `OMASHARE_WRITER=grok` is the default; set it to `claude` or
`off` to use Claude Haiku or skip AI writing. Missing tools only skip the
transcript or summary; they do not fail the upload. Filler words ("uh", "um") are left out of the transcript.
Names are saved alongside the server and key in `~/.config/omashare/config.json`.

## Install (Arch / Omarchy)

```sh
git clone https://github.com/riopierrefrancois/omashare-cli.git
cd omashare-cli/pkg && makepkg -si
```

This installs `/usr/bin/omashare`. Then run `omashare login https://your-server`.

## License

MIT. The server, an OmaShare fork of Cap Web, is AGPLv3: https://github.com/riopierrefrancois/omashare

## For apps

Run `omashare --json [--name TITLE] FILE` and read stdout line by line. Each line is one JSON event:

```json
{"event": "progress", "stage": "preparing", "percent": 37.5}
{"event": "progress", "stage": "uploading", "percent": 81.2}
{"event": "progress", "stage": "transcribing", "percent": 50.0}
{"event": "progress", "stage": "writing", "percent": 0.0}
{"event": "progress", "stage": "writing", "percent": 100.0}
{"event": "warning", "message": "Couldn't write a summary: Grok didn't answer."}
{"event": "done", "id": "kxed3ytz2z7ht2t", "link": "https://share.example.com/s/kxed3ytz2z7ht2t", "title": "Weekly update"}
{"event": "error", "error": "Not signed in. Run: omashare login"}
```

`transcribing` appears only if transcription continues after upload. `warning`
means a details step was skipped but the upload succeeded. The exit code is 0
on success and 1 on upload error. The link is already on the clipboard when
`done` arrives; pass `--no-copy` to skip that.

Every upload, including `--json` uploads, also writes a job file at
`$XDG_RUNTIME_DIR/omashare/jobs/<pid>.json` (or `/tmp/omashare-$UID/jobs/`
when no runtime directory is set). It is replaced atomically as the share
progresses and remains in `done` or `failed` state when the command exits.
At startup, OmaShare removes finished jobs older than ten minutes and running
jobs whose process has gone away. A bar widget can watch these files to show
shares in progress without depending on the command's output.

`recent --json` prints an object with `server`, `dashboard`, `unread`, and
`videos` (up to 30). Each video has its title, link, date, duration, counts,
and a local thumbnail path when one is available. `activity --json` prints
`unread` and `items` (up to 50). Use `activity --read --all` or
`activity --read ID [ID ...]` to mark items as seen. These commands print
`{"error":"…"}` and exit 1 if they cannot fetch the information.

## How it talks to the server

It does what Cap's own desktop app and CLI do:

1. Sign in: `GET /api/desktop/session/request?type=api_key&platform=web&port=N` sends the browser back to a one-shot listener on `127.0.0.1:N` with an API key.
2. `GET /api/desktop/video/create?recordingMode=desktopMP4&name=…` creates the video and returns its id and share link.
3. `POST /api/upload/signed` with `subpath: "result.mp4"` returns a presigned storage URL.
4. `PUT` the MP4 to that URL.
5. `POST /api/desktop/video/ai-content` sends the WEBVTT and any written
   title, summary, and chapters.

## Local test server

```sh
git clone --depth 1 https://github.com/CapSoftware/Cap.git ~/src/cap
cd ~/src/cap   # put random secrets in .env first (see Cap's self-hosting docs)
docker compose up -d --build
omashare login http://localhost:3000
```

Until email is set up, login codes show in `docker logs cap-web`.
