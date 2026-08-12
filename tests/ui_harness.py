from __future__ import annotations

from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meet_control


def fake_health():
    return {"ready": True, "checks": [{"name": "Teste", "ok": True, "detail": "Ambiente de teste pronto."}]}


def fake_launch(data):
    meeting = {
        "id": "ui-test", "name": data.get("name") or "Teste UI", "url": data["url"],
        "status": "starting", "created_at": meet_control.now(), "logs": ["Robô de teste criado."],
    }
    meet_control.MEETINGS[meeting["id"]] = meeting
    return meeting


if __name__ == "__main__":
    meet_control.MEETINGS.clear()
    meet_control.runtime_health = fake_health
    meet_control.launch_bot = fake_launch
    server = ThreadingHTTPServer(("127.0.0.1", 8793), partial(meet_control.App, directory=str(meet_control.ROOT)))
    server.serve_forever()
