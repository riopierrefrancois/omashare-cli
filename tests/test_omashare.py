import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
import wave
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

    def test_parakeet_word_and_cue_boundaries(self):
        words = share.parakeet_words(
            [" Work", "hive", ".", " Next", " topic", "!"],
            [0, 0.2, 0.4, 1, 1.5, 1.7], 3,
        )
        self.assertEqual([word[2] for word in words],
                         ["Workhive.", "Next", "topic!"])
        self.assertEqual([cue[2] for cue in share.words_to_cues(words)],
                         ["Workhive.", "Next topic!"])
        self.assertEqual(len(share.words_to_cues(
            [(0, 1, "First"), (7, 8, "second"), (9, 10, "third")])), 2)
        self.assertEqual(len(share.words_to_cues(
            [(0, 1, "a" * 50), (1, 2, "b" * 40), (2, 3, "last")])), 2)


class FakeServer(BaseHTTPRequestHandler):
    requests = None

    def respond(self, value, status=200):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.requests.append(("GET", self.path, None))
        if self.server.fail_create:
            deadline = time.monotonic() + 1
            while not self.server.whisper_started.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.respond({"error": "creation failed"}, 500)
            return
        self.respond({"id": "test-id", "share_url": "http://127.0.0.1/s/test-id"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append(("POST", self.path, body))
        if self.path == "/api/upload/signed":
            self.respond({"presignedPutData": {"url": "http://127.0.0.1:"
                          f"{self.server.server_port}/storage/{body['subpath']}"}})
        elif self.server.fail_ai:
            self.respond({"error": "AI unavailable"}, 500)
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
        cls.make_script("slow-whisper", """#!/usr/bin/env python3
import os, time
open(os.environ["FAKE_WHISPER_STARTED"], "w").close()
time.sleep(10)
""")
        cls.make_script("noisy-whisper", """#!/usr/bin/env python3
import json, os
os.write(2, b"x" * 200000)
print(json.dumps({"start":0,"end":1,"text":"Noisy"}), flush=True)
print(json.dumps({"progress":50}), flush=True)
print(json.dumps({"start":1,"end":2,"text":"Done"}), flush=True)
""")
        cls.make_script("parakeet", """#!/usr/bin/env python3
import json, os, sys
assert os.environ["HF_HUB_OFFLINE"] == "1"
assert os.path.isdir(sys.argv[4])
for audio in sys.argv[5:]:
    assert os.path.isfile(audio)
    print(json.dumps({"tokens":[" Work","hive","."," Next"," topic","!"],
                      "timestamps":[0,0.2,0.4,1,1.5,1.7]}), flush=True)
""")
        cls.make_script("grok", """#!/usr/bin/env python3
import json, os, sys
assert "--effort" in sys.argv and sys.argv[sys.argv.index("--effort") + 1] == "low"
prompt = sys.stdin.read()
if os.environ.get("FAKE_EXPECT_PARAKEET"):
    assert "Workhive." in prompt
else:
    assert "Hello" in prompt and "Goodbye" in prompt
if os.environ.get("FAKE_EXPECT_NAME"):
    assert "Spell these names exactly as written: Workhive." in prompt
if os.environ.get("FAKE_GROK_FAIL"):
    sys.exit(1)
print(json.dumps({"text":json.dumps({"title":"A better name",
    "summary":"See how it works.", "chapters":[]})}))
""")
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), FakeServer)
        cls.http.fail_create = False
        cls.http.fail_ai = False
        cls.http.whisper_started = cls.directory / "whisper-started"
        cls.model = cls.directory / "parakeet-model"
        cls.model.mkdir()
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
        self.http.fail_create = False
        self.http.fail_ai = False
        self.http.whisper_started.unlink(missing_ok=True)
        self.config = self.directory / "config" / "omashare" / "config.json"
        self.config.unlink(missing_ok=True)

    def run_upload(self, *args, fail=False, whisper="whisper", extra_env=None,
                   expected_returncode=0, timeout=30):
        env = os.environ.copy()
        env.update({"PATH": f"{self.bin}:{env['PATH']}",
                    "OMASHARE_SERVER": f"http://127.0.0.1:{self.http.server_port}",
                    "OMASHARE_KEY": "test-key",
                    "OMASHARE_WHISPER_PYTHON": str(self.bin / whisper),
                    "OMASHARE_SPEECH_PYTHON": str(self.bin / "absent-parakeet"),
                    "XDG_CONFIG_HOME": str(self.directory / "config"),
                    "OMASHARE_WRITER": "grok"})
        if extra_env:
            env.update(extra_env)
        if fail:
            env["FAKE_GROK_FAIL"] = "1"
        else:
            env.pop("FAKE_GROK_FAIL", None)
        done = subprocess.run([str(ROOT / "omashare"), "--json", "upload",
                               str(self.video), "--no-copy", *args], env=env,
                              capture_output=True, text=True, timeout=timeout)
        self.assertEqual(done.returncode, expected_returncode, done.stderr + done.stdout)
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
        self.assertIn("00:00:00.000 --> 00:00:01.000\nHello", payload["vtt"])
        self.assertIn("00:00:01.000 --> 00:00:02.500\nGoodbye", payload["vtt"])
        self.assertTrue(any(event["stage"] == "transcribing" and event["percent"] > 0
                            for event in events if event["event"] == "progress"))

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

    def test_missing_whisper_warns_but_completes(self):
        events = self.run_upload(
            extra_env={"HOME": str(self.directory / "empty-home"),
                       "OMASHARE_WHISPER_PYTHON": ""},
        )
        self.assertIn({"event": "warning",
                       "message": "No transcript: Whisper isn't set up on this computer."},
                      events)
        self.assertEqual(events[-1]["event"], "done")
        self.assertFalse(any(path == "/api/desktop/video/ai-content"
                             for _, path, _ in FakeServer.requests))

    def test_ai_post_failure_warns_and_keeps_original_name(self):
        self.http.fail_ai = True
        events = self.run_upload("--name", "Original")
        self.assertEqual(events[-2]["event"], "warning")
        self.assertIn("Couldn't save transcript and summary", events[-2]["message"])
        self.assertEqual(events[-1]["event"], "done")
        self.assertEqual(events[-1]["title"], "Original")

    def test_upload_failure_cancels_slow_whisper_without_waiting(self):
        self.http.fail_create = True
        started = time.monotonic()
        events = self.run_upload(
            whisper="slow-whisper",
            extra_env={"FAKE_WHISPER_STARTED": str(self.http.whisper_started)},
            expected_returncode=1, timeout=2,
        )
        self.assertLess(time.monotonic() - started, 2)
        self.assertTrue(self.http.whisper_started.exists())
        self.assertEqual(events[-1]["event"], "error")
        self.assertFalse(any(event["event"] == "done" for event in events))

    def test_large_whisper_stderr_does_not_block(self):
        events = self.run_upload(whisper="noisy-whisper", timeout=5)
        self.assertEqual(events[-1]["event"], "done")
        payload = next(body for _, path, body in FakeServer.requests
                       if path == "/api/desktop/video/ai-content")
        self.assertIn("Noisy", payload["vtt"])

    def test_parakeet_precedes_whisper_and_posts_cues(self):
        events = self.run_upload(
            whisper="absent-whisper",
            extra_env={"OMASHARE_SPEECH_PYTHON": str(self.bin / "parakeet"),
                       "OMASHARE_PARAKEET_MODEL": str(self.model),
                       "FAKE_EXPECT_PARAKEET": "1"},
        )
        self.assertEqual(events[-1]["event"], "done")
        payload = next(body for _, path, body in FakeServer.requests
                       if path == "/api/desktop/video/ai-content")
        self.assertIn("Workhive.", payload["vtt"])
        self.assertIn("Next topic!", payload["vtt"])

    def test_parakeet_piece_offsets_and_progress(self):
        wav = self.directory / "long-silence.wav"
        with wave.open(str(wav), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            for _ in range(61):
                audio.writeframes(b"\x00" * 320000)
        percentages = []
        env = os.environ.copy()
        env["PATH"] = f"{self.bin}:{env['PATH']}"
        cues = share.transcribe_parakeet(
            str(wav), 610, str(self.bin / "parakeet"), str(self.model),
            str(self.directory), env, None, percentages.append,
        )
        self.assertEqual([round(cue[0]) for cue in cues], [0, 1, 300, 301, 600, 601])
        self.assertEqual([round(value) for value in percentages], [49, 98, 100])
        self.assertEqual(cues[0][2], "Workhive.")
        self.assertEqual(cues[1][2], "Next topic!")
        wav.unlink()

    def test_names_replacement_and_prompt(self):
        cues = [(0, 2, "WorkHive, work hive, Work-hive, workhives and Networkhive.")]
        corrected = share.correct_names(cues, ["Workhive"])
        self.assertEqual(corrected[0][2],
                         "Workhive, Workhive, Workhive, workhives and Networkhive.")
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(json.dumps({"server": "local", "api_key": "secret",
                                            "names": ["Workhive"]}))
        events = self.run_upload(extra_env={"FAKE_EXPECT_NAME": "1"})
        self.assertEqual(events[-1]["event"], "done")

    def test_names_commands_preserve_credentials(self):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(json.dumps({"server": "local", "api_key": "secret"}))
        env = os.environ.copy()
        env["XDG_CONFIG_HOME"] = str(self.directory / "config")
        def command(*args):
            return subprocess.run([str(ROOT / "omashare"), "names", *args], env=env,
                                  capture_output=True, text=True, check=True).stdout

        command("add", "Workhive")
        command("add", "workhive")
        self.assertEqual(command(), "Workhive\n")
        self.assertEqual(json.loads(self.config.read_text()),
                         {"server": "local", "api_key": "secret", "names": ["Workhive"]})
        command("remove", "Workhive")
        self.assertEqual(command(), "")
        self.assertEqual(json.loads(self.config.read_text()),
                         {"server": "local", "api_key": "secret", "names": []})


if __name__ == "__main__":
    unittest.main()
