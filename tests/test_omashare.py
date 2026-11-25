import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("omashare_module", str(ROOT / "omashare"))
spec = importlib.util.spec_from_loader(loader.name, loader)
share = importlib.util.module_from_spec(spec)
loader.exec_module(share)


class TranscriptTests(unittest.TestCase):
    def test_vtt_srt_and_build(self):
        with tempfile.TemporaryDirectory() as directory:
            vtt = Path(directory) / "talk.vtt"
            vtt.write_text("WEBVTT\n\ncue-one\n00:00:01.200 --> 00:00:02.500 align:start\n"
                           "Hello\nthere\n\n00:00:03.000 --> 00:00:04.000\nBye\n")
            srt = Path(directory) / "talk.srt"
            srt.write_text("1\n00:00:01,200 --> 00:00:02,500\nHello\nthere\n\n"
                           "2\n00:00:03,000 --> 00:00:04,000\nBye\n")
            cues = [(1.2, 2.5, "Hello there"), (3.0, 4.0, "Bye")]
            self.assertEqual(share.parse_transcript(vtt), cues)
            self.assertEqual(share.parse_transcript(srt), cues)
            self.assertEqual(share.build_vtt(cues),
                             "WEBVTT\n\n00:00:01.200 --> 00:00:02.500\nHello there\n\n"
                             "00:00:03.000 --> 00:00:04.000\nBye\n")

    def test_sidecar_priority_and_explicit_override(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "talk.mp4"
            video.touch()
            meta = {"acodec": None}
            srt = video.with_suffix(".srt")
            vtt = video.with_suffix(".vtt")
            other = Path(directory) / "other.srt"
            srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nSRT\n")
            vtt.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nVTT\n")
            other.write_text("1\n00:00:01,000 --> 00:00:02,000\nEXPLICIT\n")
            self.assertEqual(share.get_transcript(str(video), meta, directory)[0][2], "VTT")
            self.assertEqual(share.get_transcript(str(video), meta, directory, str(other))[0][2],
                             "EXPLICIT")
            vtt.unlink()
            self.assertEqual(share.get_transcript(str(video), meta, directory)[0][2], "SRT")

    def test_chapter_cleaning(self):
        details = {"title": " X " * 100, "summary": " Quick guide ",
                   "chapters": [{"title": "First", "start": 3},
                                {"title": "Duplicate", "start": 3},
                                {"title": "Second", "start": 40},
                                {"title": "Past end", "start": 150},
                                {"title": "Backwards", "start": 20}]}
        cleaned = share.clean_details(details, 120)
        self.assertEqual(len(cleaned["title"]), 200)
        self.assertEqual(cleaned["summary"], "Quick guide")
        self.assertEqual(cleaned["chapters"],
                         [{"title": "First", "start": 0}, {"title": "Second", "start": 40}])


class FakeServer(BaseHTTPRequestHandler):
    requests = None

    def respond(self, value):
        data = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.requests.append(("GET", self.path, None))
        self.respond({"id": "test-id", "share_url": "http://127.0.0.1/s/test-id"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append(("POST", self.path, body))
        if self.path == "/api/upload/signed":
            self.respond({"presignedPutData": {"url": "http://127.0.0.1:"
                          f"{self.server.server_port}/storage/{body['subpath']}"}})
        else:
            self.respond({"ok": True})

    def do_PUT(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self.requests.append(("PUT", self.path, None))
        self.respond({})

    def log_message(self, *args):
        pass


class UploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.tmp.name)
        cls.video = cls.directory / "tone.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                        "-i", "color=c=blue:s=160x90:r=10:d=3",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        str(cls.video)], check=True)
        cls.bin = cls.directory / "bin"
        cls.bin.mkdir()
        cls.make_script("systemd-run", "#!/bin/sh\nshift 5\nexec \"$@\"\n")
        cls.make_script("whisper", """#!/usr/bin/env python3
import json, time
print(json.dumps({"start":0,"end":1,"text":"Hello"}), flush=True)
print(json.dumps({"progress":33.3}), flush=True)
time.sleep(0.6)
print(json.dumps({"start":1,"end":2.5,"text":"Goodbye"}), flush=True)
print(json.dumps({"progress":83.3}), flush=True)
""")
        cls.make_script("grok", """#!/usr/bin/env python3
import json, os, sys
prompt = sys.stdin.read()
assert "Hello" in prompt and "Goodbye" in prompt
if os.environ.get("FAKE_GROK_FAIL"):
    sys.exit(1)
print(json.dumps({"text":json.dumps({"title":"A better name",
    "summary":"See how it works.", "chapters":[]})}))
""")
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), FakeServer)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()
        cls.tmp.cleanup()

    @classmethod
    def make_script(cls, name, text):
        path = cls.bin / name
        path.write_text(text)
        path.chmod(0o755)

    def setUp(self):
        FakeServer.requests = []

    def run_upload(self, *args, fail=False):
        env = os.environ.copy()
        env.update({"PATH": f"{self.bin}:{env['PATH']}",
                    "OMASHARE_SERVER": f"http://127.0.0.1:{self.http.server_port}",
                    "OMASHARE_KEY": "test-key",
                    "OMASHARE_WHISPER_PYTHON": str(self.bin / "whisper"),
                    "OMASHARE_WRITER": "grok"})
        if fail:
            env["FAKE_GROK_FAIL"] = "1"
        else:
            env.pop("FAKE_GROK_FAIL", None)
        done = subprocess.run([str(ROOT / "omashare"), "--json", "upload",
                               str(self.video), "--no-copy", *args], env=env,
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        return [json.loads(line) for line in done.stdout.splitlines()]

    def test_grok_output_and_event_order(self):
        events = self.run_upload("--name", "Fallback")
        stages = [event["stage"] for event in events if event["event"] == "progress"]
        self.assertLess(stages.index("preparing"), stages.index("uploading"))
        self.assertLess(stages.index("uploading"), stages.index("transcribing"))
        self.assertLess(stages.index("transcribing"), stages.index("writing"))
        self.assertEqual(events[-1]["event"], "done")
        self.assertEqual(events[-1]["title"], "A better name")
        self.assertTrue(all(isinstance(event["percent"], (int, float))
                            for event in events if event["event"] == "progress"))
        paths = [request[1] for request in FakeServer.requests]
        self.assertTrue(any(path.startswith("/api/desktop/video/create?") for path in paths))
        self.assertIn("/storage/result.mp4", paths)
        self.assertIn("/storage/screenshot/screen-capture.jpg", paths)
        payload = next(body for method, path, body in FakeServer.requests
                       if path == "/api/desktop/video/ai-content")
        self.assertEqual(payload["title"], "A better name")
        self.assertEqual(payload["videoId"], "test-id")
        self.assertIn("WEBVTT", payload["vtt"])

    def test_keep_name_and_writer_failure(self):
        events = self.run_upload("--name", "Mine", "--keep-name")
        self.assertEqual(events[-1]["title"], "Mine")
        payload = next(body for method, path, body in FakeServer.requests
                       if path == "/api/desktop/video/ai-content")
        self.assertNotIn("title", payload)
        self.assertIn("summary", payload)
        FakeServer.requests = []
        events = self.run_upload(fail=True)
        self.assertEqual(events[-2]["event"], "warning")
        self.assertEqual(events[-1]["event"], "done")
        payload = next(body for method, path, body in FakeServer.requests
                       if path == "/api/desktop/video/ai-content")
        self.assertNotIn("summary", payload)
        self.assertIn("WEBVTT", payload["vtt"])

    def test_no_details(self):
        events = self.run_upload("--no-details")
        self.assertEqual(events[-1]["title"], "tone")
        self.assertFalse(any(path == "/api/desktop/video/ai-content"
                             for _, path, _ in FakeServer.requests))


if __name__ == "__main__":
    unittest.main()
