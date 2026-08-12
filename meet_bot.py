#!/usr/bin/env python3
"""Robô local de Google Meet com Playwright, FFmpeg e Whisper opcional.

Não usa API do Google nem armazena senha. O primeiro login é feito na janela
do Chromium e permanece somente no diretório de perfil informado.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlparse

STOP_REQUESTED = threading.Event()
JOIN_PATTERNS = (r"^join now$", r"^ask to join$", r"^participar agora$", r"^pedir para participar$", r"^solicitar participa")
LEAVE_PATTERN = re.compile(r"leave call|leave meeting|sair da chamada|sair da reuni", re.I)
MIC_OFF_PATTERN = re.compile(r"turn off microphone|desativar microfone", re.I)
CAMERA_OFF_PATTERN = re.compile(r"turn off camera|desativar c.mera", re.I)
DENIED_PATTERN = re.compile(r"can't join|cannot join|n.o foi poss.vel participar|n.o pode participar|reuni.o encerrada", re.I)


class BotError(RuntimeError):
    pass


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def fail(message: str) -> NoReturn:
    raise BotError(message)


def validate_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname != "meet.google.com":
        fail("A URL precisa ser um link https://meet.google.com/...")
    if not re.fullmatch(r"/[a-z]{3,4}-[a-z]{3,4}-[a-z]{3,4}", parsed.path):
        fail("O código do link do Meet não parece válido.")
    return value


def detect_monitor_source() -> str:
    """Retorna o monitor do sink padrão, sem pedir configuração ao operador."""
    try:
        source_result = subprocess.run(["pactl", "list", "short", "sources"], text=True,
                                       capture_output=True, check=True, timeout=8)
        monitors = [line.split()[1] for line in source_result.stdout.splitlines()
                    if len(line.split()) > 1 and line.split()[1].endswith(".monitor")]
        sink_result = subprocess.run(["pactl", "get-default-sink"], text=True,
                                     capture_output=True, check=True, timeout=8)
    except FileNotFoundError as error:
        fail("pactl não foi encontrado. Instale PulseAudio/PipeWire e pactl.")
    except subprocess.CalledProcessError as error:
        fail("Não foi possível consultar o áudio do PulseAudio/PipeWire.")
    preferred = sink_result.stdout.strip() + ".monitor"
    if preferred in monitors:
        return preferred
    if len(monitors) == 1:
        return monitors[0]
    fail("Não foi possível detectar o monitor do áudio padrão.")


def first_aria_button(page, pattern: re.Pattern[str]):
    for selector in ("button[aria-label]", "[role=button][aria-label]"):
        for button in page.locator(selector).all():
            label = button.get_attribute("aria-label") or ""
            if pattern.search(label) and button.is_visible():
                return button
    return None


def turn_off_devices(page) -> None:
    """Clica apenas em controles que indicam que câmera/mic ainda estão ligados."""
    for name, pattern in (("microfone", MIC_OFF_PATTERN), ("câmera", CAMERA_OFF_PATTERN)):
        button = first_aria_button(page, pattern)
        if button:
            try:
                button.click(timeout=2_000)
                log(f"{name.capitalize()} desligado.")
            except Exception as error:
                log(f"Não foi possível desligar {name}: {error}")


def click_join(page) -> bool:
    for pattern in JOIN_PATTERNS:
        for button in page.get_by_role("button", name=re.compile(pattern, re.I)).all():
            if button.is_visible() and button.is_enabled():
                button.click(timeout=3_000)
                log("Pedido para entrar na reunião enviado.")
                return True
    return False


def fill_guest_name(page) -> None:
    name = os.environ.get("MEET_BOT_NAME", "Assistente de Transcrição")
    for pattern in (r"your name", r"seu nome", r"nome"):
        for field in page.get_by_placeholder(re.compile(pattern, re.I)).all():
            if field.is_visible() and field.is_enabled():
                try:
                    field.fill(name, timeout=3_000)
                    log(f"Nome de convidado definido: {name}")
                    return
                except Exception as error:
                    log(f"Campo de nome mudou durante o preenchimento; tentando novamente: {error}")


def page_denied(page) -> bool:
    try:
        body = page.locator("body").inner_text(timeout=1_000)
        return bool(DENIED_PATTERN.search(body))
    except Exception:
        return False


def wait_for_admission(page, timeout_seconds: int) -> None:
    """Aguarda a sala de espera e só inicia captura quando o Meet liberar a entrada."""
    deadline = time.monotonic() + timeout_seconds
    requested = False
    last_media_check = 0.0
    while not STOP_REQUESTED.is_set():
        if time.monotonic() > deadline:
            fail("Tempo de espera para entrar na reunião excedido.")
        if time.monotonic() - last_media_check > 3:
            turn_off_devices(page)
            last_media_check = time.monotonic()
        if first_aria_button(page, LEAVE_PATTERN):
            log("Entrada na reunião confirmada.")
            return
        if page_denied(page):
            fail("O Meet informou que o robô não pode entrar na reunião.")
        if not requested:
            fill_guest_name(page)
            requested = click_join(page)
        page.wait_for_timeout(1_000)
    fail("Entrada cancelada pelo operador.")


def leave_meeting(page) -> None:
    button = first_aria_button(page, LEAVE_PATTERN)
    if not button:
        log("Nenhum controle de saída foi encontrado; o Meet pode já ter sido encerrado.")
        return
    try:
        button.click(timeout=3_000)
        log("Saída da reunião solicitada.")
        page.wait_for_timeout(800)
    except Exception as error:
        log(f"Não foi possível acionar a saída do Meet: {error}")


@dataclass
class Recorder:
    source: str
    output: Path
    process: subprocess.Popen[str] | None = None

    def start(self) -> None:
        command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-f", "pulse", "-i", self.source,
                   "-c:a", "aac", "-b:a", "160k", str(self.output)]
        try:
            self.process = subprocess.Popen(command, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except FileNotFoundError as error:
            fail("ffmpeg não foi encontrado. Instale ffmpeg na máquina Linux do robô.")
        time.sleep(0.8)
        if self.process.poll() is not None:
            details = (self.process.stderr.read() if self.process.stderr else "").strip()
            fail(f"A gravação de áudio não iniciou: {details or 'ffmpeg encerrou inesperadamente.'}")
        log(f"Gravação local iniciada: {self.output.name}")

    def stop(self) -> None:
        if not self.process or self.process.poll() is not None:
            return
        log("Finalizando arquivo de áudio…")
        self.process.send_signal(signal.SIGINT)
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=5)


def transcribe(recording: Path) -> None:
    script = Path(__file__).with_name("transcribe_recording.py")
    if not script.exists():
        fail("transcribe_recording.py não foi encontrado.")
    log("Iniciando transcrição local com Whisper…")
    completed = subprocess.run([sys.executable, str(script), str(recording)], text=True, timeout=None)
    if completed.returncode:
        fail("A transcrição Whisper falhou. Consulte os eventos acima.")
    log("Transcrição concluída.")


def configure_signals() -> None:
    def request_stop(_signum, _frame) -> None:
        if not STOP_REQUESTED.is_set():
            log("Encerramento solicitado pelo painel.")
            STOP_REQUESTED.set()
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Entrar no Google Meet, gravar e transcrever localmente.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--profile", default="./perfil-meet")
    parser.add_argument("--audio-source", default="")
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--transcribe", action="store_true")
    parser.add_argument("--i-have-consent", action="store_true")
    parser.add_argument("--max-minutes", type=int, default=int(os.environ.get("MEET_MAX_MINUTES", "0")),
                        help="0 significa ilimitado (padrão).")
    parser.add_argument("--join-timeout", type=int, default=900, help="Tempo máximo para aprovação na sala de espera.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_signals()
    if not args.i_have_consent:
        fail("Recusado: execute apenas com autorização de todos os participantes.")
    if args.max_minutes < 0:
        fail("--max-minutes deve ser 0 (ilimitado) ou um número positivo.")
    validate_url(args.url)
    if not os.environ.get("DISPLAY"):
        fail("É necessária uma sessão gráfica X11/Wayland com DISPLAY para abrir o Chromium.")

    recordings_dir = Path(os.environ.get("MEET_RECORDINGS_DIR", "recordings")).resolve()
    recordings_dir.mkdir(parents=True, exist_ok=True)
    audio_source = args.audio_source or detect_monitor_source()
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    recording = recordings_dir / f"meet_{timestamp}.m4a"
    recorder = Recorder(audio_source, recording)
    context = None
    page = None
    joined = False
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        fail("Playwright não está instalado. Execute: pip install playwright && playwright install chromium")

    try:
        with sync_playwright() as playwright:
            profile = Path(args.profile).resolve()
            profile.mkdir(parents=True, exist_ok=True)
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile), headless=False,
                args=[
                    "--window-size=1920,1080", "--autoplay-policy=no-user-gesture-required",
                    "--no-sandbox", "--disable-dev-shm-usage",
                ],
                viewport={"width": 1920, "height": 1080},
            )
            page = context.pages[0] if context.pages else context.new_page()
            log("Abrindo Google Meet. Faça login manualmente na primeira execução, se necessário.")
            page.goto(args.url, wait_until="domcontentloaded", timeout=60_000)
            wait_for_admission(page, args.join_timeout)
            joined = True
            if args.record:
                recorder.start()
            deadline = time.monotonic() + args.max_minutes * 60 if args.max_minutes else None
            log("Robô ativo. A duração é ilimitada; use 'Remover robô' para encerrar.")
            while not STOP_REQUESTED.is_set():
                if deadline and time.monotonic() >= deadline:
                    log("Limite de duração atingido.")
                    break
                if not first_aria_button(page, LEAVE_PATTERN):
                    log("O Meet não mostra mais a chamada; a reunião parece encerrada.")
                    break
                page.wait_for_timeout(1_000)
            if joined:
                leave_meeting(page)
            recorder.stop()
        if args.transcribe and recording.exists() and recording.stat().st_size > 0:
            transcribe(recording)
        elif args.transcribe:
            fail("Não há gravação válida para transcrever.")
        return 0
    finally:
        recorder.stop()
        if context:
            try:
                context.close()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BotError as error:
        log(f"ERRO: {error}")
        raise SystemExit(1)
    except Exception as error:
        log(f"ERRO INESPERADO: {error}")
        raise SystemExit(1)
