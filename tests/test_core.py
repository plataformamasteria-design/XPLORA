from __future__ import annotations

import io
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import meet_bot
import meet_control
from transcribe_recording import srt_timestamp


class FakeProcess:
    def __init__(self, lines=(), code=0):
        self.stdout = io.StringIO("\n".join(lines) + ("\n" if lines else ""))
        self.code = code
        self.pid = 1234
        self.signals = []

    def wait(self):
        return self.code

    def poll(self):
        return None

    def send_signal(self, value):
        self.signals.append(value)


class CoreTests(unittest.TestCase):
    def setUp(self):
        meet_control.MEETINGS.clear()
        meet_control.PROCESSES.clear()

    def test_meet_url_validation(self):
        valid = "https://meet.google.com/abc-defg-hij"
        self.assertEqual(meet_control.validate_meet_url(valid), valid)
        self.assertEqual(meet_bot.validate_url(valid), valid)
        for invalid in ("http://meet.google.com/abc-defg-hij", "https://evil.example/abc-defg-hij", "https://meet.google.com/not-a-code"):
            with self.assertRaises((ValueError, meet_bot.BotError)):
                meet_control.validate_meet_url(invalid)

    def test_default_audio_monitor_detection(self):
        sources = "1\talsa_output.primary.monitor\tPipeWire\n2\talsa_input.mic\tPipeWire\n"
        results = [SimpleNamespace(stdout=sources), SimpleNamespace(stdout="alsa_output.primary\n")]
        with patch("meet_control.subprocess.run", side_effect=results):
            self.assertEqual(meet_control.detect_monitor_source(), "alsa_output.primary.monitor")

    def test_transcription_status_survives_process_exit(self):
        meeting_id = "meeting-1"
        process = FakeProcess([
            "Entrada na reunião confirmada.",
            "Iniciando transcrição local com Whisper…",
            "Transcrição concluída.",
        ])
        meet_control.MEETINGS[meeting_id] = {"id": meeting_id, "status": "starting", "logs": [], "stop_requested": True}
        meet_control.PROCESSES[meeting_id] = process
        finished = threading.Event()

        def save():
            if meeting_id not in meet_control.PROCESSES:
                finished.set()

        with patch("meet_control.save_meetings", side_effect=save):
            meet_control.new_log_reader(meeting_id, process, "bot")
            self.assertTrue(finished.wait(2))
        self.assertEqual(meet_control.MEETINGS[meeting_id]["status"], "transcribed")

    def test_single_active_bot_guard(self):
        meet_control.MEETINGS["active"] = {"id": "active", "name": "Atual", "status": "starting"}
        meet_control.PROCESSES["active"] = FakeProcess()
        with self.assertRaisesRegex(ValueError, "Já existe um robô"):
            meet_control.launch_bot({"consent": True, "url": "https://meet.google.com/abc-defg-hij"})

    def test_stop_signals_only_bot_for_graceful_recording_close(self):
        process = FakeProcess()
        meet_control.MEETINGS["active"] = {"id": "active", "name": "Atual", "status": "in_meeting"}
        meet_control.PROCESSES["active"] = process
        with patch("meet_control.threading.Thread") as thread:
            result = meet_control.stop_bot("active")
        self.assertEqual(process.signals, [meet_control.signal.SIGTERM])
        self.assertEqual(result["status"], "stopping")
        thread.assert_called_once()

    def test_srt_timestamp(self):
        self.assertEqual(srt_timestamp(3661.125), "01:01:01,125")

    def test_recording_artifacts_are_discovered(self):
        with tempfile.TemporaryDirectory(dir=meet_control.ROOT) as folder:
            directory = Path(folder)
            audio = directory / "sample.m4a"
            audio.write_bytes(b"audio")
            audio.with_suffix(".txt").write_text("texto", encoding="utf-8")
            with patch.object(meet_control, "DEFAULT_RECORDINGS_DIR", directory):
                result = meet_control.audio_files(directory)
            self.assertEqual(result[0]["artifacts"][0]["type"], "Texto")


if __name__ == "__main__":
    unittest.main()
