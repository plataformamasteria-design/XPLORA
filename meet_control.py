#!/usr/bin/env python3
"""Painel local para controlar o MVP de gravação de Google Meet.

Execute em uma sessão gráfica Linux:
    python3 meet_control.py

O arquivo meet_bot.py deve ficar nesta mesma pasta. Ele nunca armazena senhas:
o login do Google continua sendo feito manualmente no perfil persistente do bot.
"""

from __future__ import annotations

import json
import base64
import hmac
import importlib.util
import mimetypes
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from functools import partial
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent
STORAGE_DIR = Path(os.environ.get("MEET_STORAGE_DIR", str(ROOT / "storage"))).resolve()
DATA_DIR = STORAGE_DIR / "data"
MEETINGS_FILE = DATA_DIR / "meetings.json"
DEFAULT_RECORDINGS_DIR = STORAGE_DIR / "recordings"
DEFAULT_PROFILE_DIR = STORAGE_DIR / "perfil-meet"
BOT_FILE = ROOT / "meet_bot.py"
TRANSCRIBER_FILE = ROOT / "transcribe_recording.py"
LOCK = threading.RLock()
LAUNCH_LOCK = threading.Lock()
PROCESSES: dict[str, subprocess.Popen[str]] = {}
MEETINGS: dict[str, dict] = {}
ALLOWED_AUDIO = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".webm", ".mp4", ".mkv"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_meetings() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp = MEETINGS_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(list(MEETINGS.values()), ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(MEETINGS_FILE)


def load_meetings() -> None:
    if not MEETINGS_FILE.exists():
        return
    try:
        for meeting in json.loads(MEETINGS_FILE.read_text(encoding="utf-8")):
            # Nenhum processo sobrevive a uma reinicialização do painel.
            if meeting.get("status") in {"starting", "in_meeting", "stopping", "transcribing"}:
                meeting["status"] = "interrupted"
                meeting["finished_at"] = now()
            MEETINGS[meeting["id"]] = meeting
        save_meetings()
    except (json.JSONDecodeError, KeyError):
        # Não apagar o histórico se o arquivo tiver sido editado/corrompido.
        bad = MEETINGS_FILE.with_suffix(f".invalid-{int(time.time())}.json")
        MEETINGS_FILE.replace(bad)


def safe_local_dir(value: str, default: Path) -> Path:
    """Permite apenas diretórios dentro desta instalação do painel."""
    candidate = (ROOT / value).resolve() if value else default.resolve()
    try:
        candidate.relative_to(ROOT)
    except ValueError as error:
        raise ValueError("Use uma pasta dentro da instalação do painel.") from error
    return candidate


def validate_meet_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or parsed.hostname != "meet.google.com":
        raise ValueError("Informe um link https://meet.google.com/... válido.")
    if not re.fullmatch(r"/[a-z]{3,4}-[a-z]{3,4}-[a-z]{3,4}", parsed.path):
        raise ValueError("O código da reunião do Google Meet não parece válido.")
    return value.strip()


def detect_monitor_source() -> str:
    """Usa automaticamente o monitor do dispositivo de saída padrão."""
    try:
        sources_result = subprocess.run(
            ["pactl", "list", "short", "sources"], text=True, capture_output=True,
            check=True, timeout=8,
        )
        sources = [line.split()[1] for line in sources_result.stdout.splitlines()
                   if len(line.split()) > 1 and line.split()[1].endswith(".monitor")]
        if not sources:
            raise ValueError("Nenhuma fonte monitor foi encontrada pelo PulseAudio/PipeWire.")
        sink_result = subprocess.run(
            ["pactl", "get-default-sink"], text=True, capture_output=True,
            check=True, timeout=8,
        )
        preferred = sink_result.stdout.strip() + ".monitor"
        if preferred in sources:
            return preferred
        if len(sources) == 1:
            return sources[0]
        raise ValueError("Não foi possível identificar o áudio de saída padrão automaticamente.")
    except FileNotFoundError as error:
        raise ValueError("PulseAudio/PipeWire não foi encontrado. Instale pactl na máquina Linux do robô.") from error
    except subprocess.CalledProcessError as error:
        raise ValueError("Não foi possível consultar o áudio do sistema. Verifique se a sessão PulseAudio/PipeWire está ativa.") from error
    except subprocess.TimeoutExpired as error:
        raise ValueError("A detecção de áudio excedeu o tempo esperado.") from error


def runtime_health() -> dict:
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    add("Robô", BOT_FILE.is_file(), "meet_bot.py disponível." if BOT_FILE.is_file() else "meet_bot.py ausente.")
    add("Transcrição", TRANSCRIBER_FILE.is_file(), "Whisper integrado." if TRANSCRIBER_FILE.is_file() else "transcribe_recording.py ausente.")
    add("FFmpeg", bool(shutil.which("ffmpeg")), "FFmpeg disponível." if shutil.which("ffmpeg") else "FFmpeg não encontrado.")
    add("PulseAudio", bool(shutil.which("pactl")), "pactl disponível." if shutil.which("pactl") else "pactl não encontrado.")
    add("Tela virtual", bool(os.environ.get("DISPLAY")), f"DISPLAY={os.environ.get('DISPLAY')}" if os.environ.get("DISPLAY") else "DISPLAY não configurado.")
    add("Playwright", importlib.util.find_spec("playwright") is not None, "Playwright instalado." if importlib.util.find_spec("playwright") else "Playwright não instalado.")
    add("Whisper local", importlib.util.find_spec("faster_whisper") is not None, "faster-whisper instalado." if importlib.util.find_spec("faster_whisper") else "faster-whisper não instalado.")
    try:
        STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        add("Armazenamento", os.access(STORAGE_DIR, os.W_OK), f"Persistência em {STORAGE_DIR}.")
    except OSError as error:
        add("Armazenamento", False, f"Armazenamento indisponível: {error}")
    try:
        add("Áudio", True, f"Fonte automática: {detect_monitor_source()}")
    except ValueError as error:
        add("Áudio", False, str(error))
    return {"ready": all(item["ok"] for item in checks), "checks": checks}


def new_log_reader(meeting_id: str, process: subprocess.Popen[str], mode: str) -> None:
    def read_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            with LOCK:
                meeting = MEETINGS.get(meeting_id)
                if not meeting:
                    return
                event = line.rstrip()
                meeting["logs"] = (meeting.get("logs", []) + [event])[-200:]
                if "Entrada na reunião confirmada" in event:
                    meeting["status"] = "in_meeting"
                    meeting["joined_at"] = now()
                elif "Iniciando transcrição local" in event:
                    meeting["status"] = "transcribing"
                elif "Transcrição concluída" in event:
                    meeting["status"] = "transcribed"
                    meeting["transcribed_at"] = now()
                save_meetings()
        code = process.wait()
        with LOCK:
            meeting = MEETINGS.get(meeting_id)
            if not meeting:
                return
            intentional = bool(meeting.get("stop_requested"))
            if mode == "bot":
                if code == 0 and meeting.get("status") == "transcribed":
                    pass
                elif code == 0:
                    meeting["status"] = "removed" if intentional else "finished"
                elif meeting.get("status") == "transcribing":
                    meeting["status"] = "transcription_failed"
                else:
                    meeting["status"] = "failed"
                meeting["finished_at"] = now()
            else:
                meeting["status"] = "transcribed" if code == 0 else "transcription_failed"
                meeting["transcribed_at"] = now()
            meeting["exit_code"] = code
            PROCESSES.pop(meeting_id, None)
            save_meetings()
    threading.Thread(target=read_output, daemon=True).start()


def _launch_bot(data: dict) -> dict:
    if not data.get("consent"):
        raise ValueError("Confirme que todos os participantes autorizaram a gravação.")
    if not BOT_FILE.exists():
        raise ValueError("meet_bot.py não foi encontrado. Coloque o MVP na mesma pasta deste painel.")

    url = validate_meet_url(str(data.get("url", "")))
    audio_source = detect_monitor_source()

    default_profile = DEFAULT_PROFILE_DIR.relative_to(ROOT).as_posix()
    default_recordings = DEFAULT_RECORDINGS_DIR.relative_to(ROOT).as_posix()
    profile = safe_local_dir(str(data.get("profile", default_profile)), DEFAULT_PROFILE_DIR)
    recordings_dir = safe_local_dir(str(data.get("recordings_dir", default_recordings)), DEFAULT_RECORDINGS_DIR)
    profile.mkdir(parents=True, exist_ok=True)
    recordings_dir.mkdir(parents=True, exist_ok=True)

    meeting_id = str(uuid.uuid4())
    meeting = {
        "id": meeting_id,
        "url": url,
        "name": str(data.get("name", "")).strip() or url.rsplit("/", 1)[-1],
        "status": "starting",
        "created_at": now(),
        "max_minutes": None,
        "audio_source": audio_source,
        "recordings_dir": recordings_dir.relative_to(ROOT).as_posix(),
        "profile": profile.relative_to(ROOT).as_posix(),
        "logs": ["Iniciando o navegador automatizado…", f"Áudio detectado automaticamente: {audio_source}", "Sem limite de duração solicitado."],
    }
    command = [
        sys.executable, str(BOT_FILE), "--url", url, "--profile", str(profile),
        "--record", "--transcribe", "--i-have-consent", "--audio-source", audio_source,
    ]
    environment = os.environ.copy()
    environment["MEET_RECORDINGS_DIR"] = str(recordings_dir)
    environment["MEET_DIAGNOSTICS_DIR"] = str(DATA_DIR)
    environment["MEET_MAX_MINUTES"] = "0"  # Contrato: 0 significa sem limite.
    try:
        process = subprocess.Popen(
            command, cwd=ROOT, env=environment, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as error:
        raise ValueError(f"Não foi possível iniciar o robô: {error}") from error
    with LOCK:
        MEETINGS[meeting_id] = meeting
        PROCESSES[meeting_id] = process
        save_meetings()
    new_log_reader(meeting_id, process, "bot")
    return meeting


def launch_bot(data: dict) -> dict:
    with LAUNCH_LOCK:
        with LOCK:
            active = [
                MEETINGS.get(meeting_id, {}).get("name", meeting_id)
                for meeting_id, process in PROCESSES.items()
                if process.poll() is None and not MEETINGS.get(meeting_id, {}).get("recording")
            ]
        if active:
            raise ValueError(f"Já existe um robô em execução: {active[0]}. Remova-o antes de enviar outro.")
        return _launch_bot(data)


def stop_bot(meeting_id: str) -> dict:
    with LOCK:
        meeting = MEETINGS.get(meeting_id)
        process = PROCESSES.get(meeting_id)
        if not meeting:
            raise ValueError("Reunião não encontrada.")
        if not process or process.poll() is not None:
            raise ValueError("Este robô já não está em execução.")
        meeting["status"] = "stopping"
        meeting["stop_requested"] = True
        meeting.setdefault("logs", []).append("Remoção solicitada pelo operador.")
        save_meetings()
    # Sinaliza somente o processo Python. Ele fecha a chamada e o FFmpeg de modo
    # gracioso antes de iniciar o Whisper; matar o grupo aqui também derrubaria
    # Chromium/FFmpeg cedo demais e poderia corromper a gravação.
    try:
        process.send_signal(signal.SIGTERM)
    except ProcessLookupError as error:
        raise ValueError("Este robô já encerrou.") from error

    def enforce_graceful_exit() -> None:
        for _ in range(45):
            if process.poll() is not None:
                return
            with LOCK:
                status = MEETINGS.get(meeting_id, {}).get("status")
            if status in {"transcribing", "transcribed"}:
                return
            time.sleep(1)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (AttributeError, ProcessLookupError):
            if process.poll() is None:
                process.kill()

    threading.Thread(target=enforce_graceful_exit, daemon=True).start()
    return meeting


def audio_files(recordings_dir: Path) -> list[dict]:
    if not recordings_dir.exists():
        return []
    found = []
    for item in recordings_dir.rglob("*"):
        if item.is_file() and item.suffix.lower() in ALLOWED_AUDIO:
            stat = item.stat()
            artifacts = []
            for suffix, kind in ((".txt", "Texto"), (".srt", "Legendas"), (".json", "JSON")):
                artifact = item.with_suffix(suffix)
                if artifact.is_file():
                    artifacts.append({"name": artifact.name, "type": kind, "path": artifact.relative_to(ROOT).as_posix()})
            found.append({
                "path": item.relative_to(ROOT).as_posix(),
                "name": item.name,
                "size": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
                "artifacts": artifacts,
            })
    return sorted(found, key=lambda item: item["modified_at"], reverse=True)


def list_recordings() -> list[dict]:
    dirs = {DEFAULT_RECORDINGS_DIR.resolve()}
    for meeting in MEETINGS.values():
        try:
            dirs.add(safe_local_dir(meeting.get("recordings_dir", ""), DEFAULT_RECORDINGS_DIR))
        except ValueError:
            continue
    unique: dict[str, dict] = {}
    for folder in dirs:
        for item in audio_files(folder):
            unique[item["path"]] = item
    return sorted(unique.values(), key=lambda item: item["modified_at"], reverse=True)


def launch_transcription(data: dict) -> dict:
    relative_path = str(data.get("recording", ""))
    recording = (ROOT / relative_path).resolve()
    try:
        recording.relative_to(ROOT)
    except ValueError as error:
        raise ValueError("Arquivo de gravação inválido.") from error
    if not recording.is_file() or recording.suffix.lower() not in ALLOWED_AUDIO:
        raise ValueError("Selecione uma gravação de áudio/vídeo disponível.")
    if not TRANSCRIBER_FILE.exists():
        raise ValueError("transcribe_recording.py não foi encontrado.")

    meeting_id = str(uuid.uuid4())
    meeting = {
        "id": meeting_id, "name": f"Transcrição: {recording.name}", "status": "transcribing",
        "created_at": now(), "recording": recording.relative_to(ROOT).as_posix(),
        "logs": ["Preparando transcrição local com Whisper…"],
    }
    command = [sys.executable, str(TRANSCRIBER_FILE), str(recording)]
    process = subprocess.Popen(command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, start_new_session=True)
    with LOCK:
        MEETINGS[meeting_id] = meeting
        PROCESSES[meeting_id] = process
        save_meetings()
    new_log_reader(meeting_id, process, "transcription")
    return meeting


class App(SimpleHTTPRequestHandler):
    def authenticated(self) -> bool:
        password = os.environ.get("PANEL_PASSWORD", "")
        if not password:
            return True
        username = os.environ.get("PANEL_USERNAME", "admin")
        expected = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        return hmac.compare_digest(self.headers.get("Authorization", ""), expected)

    def require_auth(self) -> bool:
        if self.authenticated():
            return False
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="Meet Control"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def send_json(self, payload: object, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_local_file(self, path: Path, download: bool = False) -> None:
        if not path.is_file():
            self.send_json({"error": "Arquivo não encontrado."}, HTTPStatus.NOT_FOUND)
            return
        size = path.stat().st_size
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith("text/") or mime == "application/json" else ""))
        self.send_header("Content-Length", str(size))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        if download:
            safe_name = path.name.replace('"', "")
            self.send_header("Content-Disposition", f'attachment; filename="{safe_name}"')
        self.end_headers()
        with path.open("rb") as source:
            shutil.copyfileobj(source, self.wfile, length=1024 * 1024)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 100_000:
            raise ValueError("Solicitação grande demais.")
        return json.loads(self.rfile.read(length))

    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if route == "/healthz":
            self.send_json({"status": "ok"})
            return
        if self.require_auth():
            return
        if route == "/api/health":
            self.send_json(runtime_health())
            return
        if route == "/api/meetings":
            with LOCK:
                self.send_json(sorted(MEETINGS.values(), key=lambda item: item["created_at"], reverse=True))
            return
        if route == "/api/recordings":
            self.send_json(list_recordings())
            return
        if route in {"/", "/index.html"}:
            self.send_local_file(ROOT / "static" / "index.html")
            return
        if route.startswith("/static/"):
            candidate = (ROOT / unquote(route.lstrip("/"))).resolve()
            try:
                candidate.relative_to((ROOT / "static").resolve())
            except ValueError:
                self.send_json({"error": "Caminho inválido."}, HTTPStatus.BAD_REQUEST)
                return
            self.send_local_file(candidate)
            return
        if route.startswith("/files/"):
            candidate = (ROOT / unquote(route[len("/files/"):])).resolve()
            try:
                candidate.relative_to(STORAGE_DIR.resolve())
            except ValueError:
                self.send_json({"error": "Caminho inválido."}, HTTPStatus.BAD_REQUEST)
                return
            if candidate.suffix.lower() not in ALLOWED_AUDIO | {".txt", ".srt", ".json"}:
                self.send_json({"error": "Tipo de arquivo não permitido."}, HTTPStatus.BAD_REQUEST)
                return
            self.send_local_file(candidate, download=True)
            return
        self.send_json({"error": "Rota não encontrada."}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.require_auth():
            return
        try:
            data = self.read_json()
            if self.path == "/api/meetings":
                self.send_json(launch_bot(data), HTTPStatus.CREATED)
            elif self.path.startswith("/api/meetings/") and self.path.endswith("/stop"):
                meeting_id = self.path.split("/")[3]
                self.send_json(stop_bot(meeting_id))
            elif self.path == "/api/transcriptions":
                self.send_json(launch_transcription(data), HTTPStatus.ACCEPTED)
            else:
                self.send_json({"error": "Rota não encontrada."}, HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except BrokenPipeError:
            return
        except Exception as error:  # Exibe erro útil no painel; detalhes permanecem no terminal.
            self.send_json({"error": f"Erro interno: {error}"}, HTTPStatus.INTERNAL_SERVER_ERROR)


if __name__ == "__main__":
    load_meetings()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8787"))
    if host not in {"127.0.0.1", "localhost", "::1"} and not os.environ.get("PANEL_PASSWORD"):
        raise SystemExit("PANEL_PASSWORD é obrigatório quando o painel é exposto publicamente.")
    server = ThreadingHTTPServer((host, port), partial(App, directory=str(ROOT)))
    print(f"Painel disponível em http://{host}:{port}")
    print("Use Ctrl+C para parar o painel. Os robôs em execução receberão SIGTERM.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando robôs…")
        for meeting_id in list(PROCESSES):
            try:
                stop_bot(meeting_id)
            except ValueError:
                pass
        server.server_close()
