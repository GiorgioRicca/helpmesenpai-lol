"""Gameplan generato da un modello locale via Ollama."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator

import requests

from .prompts import SYSTEM_PROMPT

DEFAULT_MODEL = os.environ.get("LOLPICK_MODEL", "richardyoung/qwen3.6-27b-abliterated:latest")
DEFAULT_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
NO_THINK = re.compile(r"\s*/\s*no_think")


class OllamaError(Exception):
    pass


def _host(host: str) -> str:
    return host if host.startswith("http") else f"http://{host}"


def stream_gameplan(prompt: str, model: str = DEFAULT_MODEL, host: str = DEFAULT_HOST) -> Iterator[str]:
    """Restituisce il testo del modello a pezzi, man mano che viene generato."""
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
        "stream": True,
        "think": False,
        "keep_alive": 0,  # scarica il modello appena finito: libera RAM/GPU per la partita
        "options": {"temperature": 0.4},
    }
    try:
        r = requests.post(f"{_host(host)}/api/chat", json=payload, stream=True, timeout=(3, 300))
    except requests.RequestException as e:
        raise OllamaError(f"Ollama non raggiungibile su {host} (è avviato? `ollama serve`)") from e
    if r.status_code != 200:
        raise OllamaError(f"Ollama ha risposto {r.status_code}: {r.text[:200]}")

    in_think = False  # alcuni modelli emettono comunque il ragionamento tra <think> e </think>
    pending = ""
    for line in r.iter_lines():
        if not line:
            continue
        chunk = json.loads(line)
        if "error" in chunk:
            raise OllamaError(chunk["error"])
        raw, text = chunk.get("message", {}).get("content", ""), ""
        while raw:
            if in_think:
                in_think = "</think>" not in raw
                raw = "" if in_think else raw.split("</think>", 1)[1]
            elif "<think>" in raw:
                before, raw = raw.split("<think>", 1)
                text, in_think = text + before, True
            else:
                text, raw = text + raw, ""
        # qwen3 a volte ripete il "/no_think" che Ollama aggiunge al prompt: lo togliamo,
        # trattenendo la coda che potrebbe esserne l'inizio finché non arriva il resto.
        pending = NO_THINK.sub("", pending + text)
        cut = pending.rfind("/")
        hold = cut != -1 and len(pending) - cut < 12 and not chunk.get("done")
        if hold:
            text, pending = pending[:cut], pending[cut:]
        else:
            text, pending = pending, ""
        if text:
            yield text
        if chunk.get("done"):
            break
