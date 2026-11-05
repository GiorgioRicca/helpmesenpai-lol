"""Accesso in sola lettura all'API locale del client di League (LCU).

Le credenziali (porta + password) si trovano nel file `lockfile` della cartella
di installazione oppure negli argomenti del processo LeagueClientUx.
Non si tocca né il processo del gioco né i suoi file: solo richieste HTTP locali.
"""

from __future__ import annotations

import platform
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import requests
import urllib3

from .champions import ChampionDB
from .livegame import GameSnapshot, gameflow_snapshot

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

LOCKFILE_PATHS = [
    Path("/Applications/League of Legends.app/Contents/LoL/lockfile"),
    Path("C:/Riot Games/League of Legends/lockfile"),
    Path("D:/Riot Games/League of Legends/lockfile"),
]


class LCUNotRunning(Exception):
    pass


@dataclass
class Credentials:
    port: int
    password: str


def _from_lockfile() -> Credentials | None:
    for path in LOCKFILE_PATHS:
        try:
            _name, _pid, port, password, _proto = path.read_text().strip().split(":")
            return Credentials(int(port), password)
        except (OSError, ValueError):
            continue
    return None


def _from_process() -> Credentials | None:
    try:
        if platform.system() == "Windows":
            cmd = ["powershell", "-NoProfile", "-Command",
                   "Get-CimInstance Win32_Process -Filter \"name='LeagueClientUx.exe'\" | Select -Expand CommandLine"]
        else:
            cmd = ["ps", "-A", "-o", "args"]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if "LeagueClientUx" not in line:
            continue
        port = re.search(r"--app-port=(\d+)", line)
        token = re.search(r"--remoting-auth-token=([\w-]+)", line)
        if port and token:
            return Credentials(int(port.group(1)), token.group(1))
    return None


def find_credentials() -> Credentials | None:
    return _from_process() or _from_lockfile()


class LCUSource:
    """Champ select corrente e, dalla schermata di caricamento in poi, i dati di tutti i giocatori."""

    def __init__(self):
        self._http = requests.Session()
        self._http.verify = False  # il client usa un certificato self-signed di Riot
        self._base: str | None = None
        self._puuid: str | None = None

    def _connect(self) -> None:
        creds = find_credentials()
        if not creds:
            raise LCUNotRunning("Client di League non trovato")
        self._base = f"https://127.0.0.1:{creds.port}"
        self._http.auth = ("riot", creds.password)

    def get_session(self) -> dict | None:
        return self._get("/lol-champ-select/v1/session")

    def get_game_state(self, db: ChampionDB) -> GameSnapshot | None:
        """Schermata di caricamento / partita: campioni, summoner e ordine di tutti e dieci."""
        try:
            flow = self._get("/lol-gameflow/v1/session")
            if flow and self._puuid is None:
                self._puuid = (self._get("/lol-summoner/v1/current-summoner") or {}).get("puuid", "")
        except LCUNotRunning:
            return None
        return gameflow_snapshot(flow, db, self._puuid or "")

    def _get(self, path: str) -> dict | None:
        if not self._base:
            self._connect()
        try:
            r = self._http.get(f"{self._base}{path}", timeout=2)
        except requests.RequestException as e:
            self._base, self._puuid = None, None  # client chiuso o riavviato: rileggi le credenziali
            raise LCUNotRunning(str(e)) from e
        if r.status_code == 404:
            return None
        if r.status_code == 401:
            self._base = None
            raise LCUNotRunning("Credenziali LCU non valide")
        r.raise_for_status()
        return r.json()
