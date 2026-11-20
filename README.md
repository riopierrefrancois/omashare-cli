# OmaShare

Share a video as a link, Loom style, from your own server.

The server is [Cap Web](https://github.com/CapSoftware/Cap) (self-hosted). It gives each video a share page with comments, reactions and a transcript. The `omashare` command uploads videos to it. Omareel, Omacut and Omaedit call that command for their Share link button.

## Use it

```sh
omashare login https://share.example.com   # once; opens your browser to sign in
omashare talk.mp4                          # uploads, prints the link, copies it
omashare talk.mov --name "Weekly update"   # set the title on the share page
omashare status                            # which server and account
omashare logout
```

Any video ffmpeg can read works. MP4 files with H.264 and AAC go up as they are. Anything else (WebM, MKV, MOV, HEVC, 10-bit) is first converted to an MP4 every browser plays.

The key is saved in `~/.config/omashare/config.json` (readable only by you). `OMASHARE_SERVER` and `OMASHARE_KEY` override it.

Needs `python3` and `ffmpeg`. `wl-copy` is optional; it copies the link.

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
{"event": "done", "id": "kxed3ytz2z7ht2t", "link": "https://share.example.com/s/kxed3ytz2z7ht2t"}
{"event": "error", "error": "Not signed in. Run: omashare login"}
```

The exit code is 0 on success and 1 on error. The link is already on the clipboard when `done` arrives; pass `--no-copy` to skip that.

## How it talks to the server

It does what Cap's own desktop app and CLI do:

1. Sign in: `GET /api/desktop/session/request?type=api_key&platform=web&port=N` sends the browser back to a one-shot listener on `127.0.0.1:N` with an API key.
2. `GET /api/desktop/video/create?recordingMode=desktopMP4&name=…` creates the video and returns its id and share link.
3. `POST /api/upload/signed` with `subpath: "result.mp4"` returns a presigned storage URL.
4. `PUT` the MP4 to that URL.

## Local test server

```sh
git clone --depth 1 https://github.com/CapSoftware/Cap.git ~/src/cap
cd ~/src/cap   # put random secrets in .env first (see Cap's self-hosting docs)
docker compose up -d --build
omashare login http://localhost:3000
```

Until email is set up, login codes show in `docker logs cap-web`.
