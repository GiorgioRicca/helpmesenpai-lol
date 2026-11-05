"""Matchup della patch: win rate di ogni campione contro ogni avversario, per coppia di lane, in Diamond+.

Fonte: Lolalytics, scaricati da `lolpick-update` in data/matchups.json: a runtime l'app legge solo quel file.

Per ogni avversario il vantaggio è (win rate contro di lui − win rate medio del campione contro quella lane),
così conta solo l'incrocio tra i due: la forza generale del campione è già nella tier list. Viene smussato con
n/(n + PRIOR_GAMES), perché 150 partite hanno un errore di ±4 punti, e pesato in base a quanto le due lane si incontrano.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import requests

from .champions import DATA_DIR, Champion, ChampionDB
from .criteria import ROLES
from .meta import ELO, LANE_NAMES, META_URL, Meta

MATCHUPS_FILE = DATA_DIR / "matchups.json"
SLUGS = {"MonkeyKing": "wukong"}  # id Data Dragon diverso dal nome usato da Lolalytics

MIN_CHAMP_GAMES = 1000  # sotto queste partite nella lane la fonte non ha matchup: inutile chiederli
MIN_ROW_GAMES = 10  # righe più piccole sono solo rumore e peso sul file
PRIOR_GAMES = 400  # smussamento: con deviazione reale di ~2,5 punti e rumore 50/√n, K = 2500/2,5²
SAME_LANE = 1.0  # avversario diretto
BOT_LANE = 0.6  # ADC contro support e viceversa: stessa lane 2v2
OTHER_LANE = 0.25  # lane diverse: si incontrano meno
GUESSED_ROLE = 0.7  # il ruolo del nemico è dedotto, non noto
WORKERS = 4  # richieste in parallelo: è un servizio altrui, niente raffiche
MAX_FAILED = 0.05  # oltre questa quota di richieste fallite si tengono i dati precedenti


@dataclass
class MatchupStat:
    enemy: Champion
    games: int
    winrate: float  # % grezza del candidato contro questo nemico
    edge: float  # vantaggio smussato e pesato, in punti di win rate


def _lane_weight(role: str, enemy_role: str) -> float:
    if role == enemy_role:
        return SAME_LANE
    return BOT_LANE if {role, enemy_role} == {"bottom", "utility"} else OTHER_LANE


class Matchups:
    def __init__(self, patch: str, data: dict, updated: str = "", failed: int = 0):
        self.patch = patch
        self.data = data  # {ruolo: {id campione: {ruolo avversario: {id avversario: [partite, win rate]}}}}
        self.updated = updated
        self.failed = failed  # richieste non riuscite nell'ultimo download
        self._base: dict[tuple, float] = {}

    def _baseline(self, role: str, key: int, enemy_role: str, rows: dict) -> float:
        k = (role, key, enemy_role)
        if k not in self._base:
            games = sum(n for n, _ in rows.values())
            self._base[k] = sum(n * wr for n, wr in rows.values()) / games if games else 0.0
        return self._base[k]

    def against(self, champion: Champion, role: str, enemy: Champion, enemy_role: str, certain: bool = True) -> MatchupStat | None:
        rows = self.data.get(role, {}).get(str(champion.key), {}).get(enemy_role, {})
        row = rows.get(str(enemy.key))
        if not row:
            return None
        games, winrate = row
        delta = (winrate - self._baseline(role, champion.key, enemy_role, rows)) * games / (games + PRIOR_GAMES)
        weight = _lane_weight(role, enemy_role) * (1 if certain else GUESSED_ROLE)
        return MatchupStat(enemy, games, winrate, delta * weight)

    def versus(self, champion: Champion, role: str, opponents: list[tuple[Champion, str, bool]]) -> list[MatchupStat]:
        """Matchup contro i nemici lockati (campione, ruolo, ruolo certo), dal più influente."""
        stats = [s for e, r, certain in opponents if (s := self.against(champion, role, e, r, certain))]
        return sorted(stats, key=lambda s: abs(s.edge), reverse=True)


def _fetch_champion(session: requests.Session, patch: str, role: str, champ: Champion, enemy_role: str) -> dict[str, list]:
    params = {"ep": "counter", "v": 1, "patch": patch, "c": SLUGS.get(champ.id, champ.id.lower()), "lane": LANE_NAMES[role],
              "vslane": LANE_NAMES[enemy_role], "tier": ELO, "queue": "ranked", "region": "all"}
    for attempt in range(4):
        try:
            r = session.get(META_URL, params=params, timeout=20)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            data = r.json()
            if data["stats"]["cid"] != champ.key:
                raise ValueError(f"{champ.id}: risposta per un altro campione")
            return {str(row["cid"]): [row["n"], row["vsWr"]] for row in data.get("counters", []) if row["n"] >= MIN_ROW_GAMES}
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    return {}


def update_matchups(db: ChampionDB, meta: Meta, progress: Callable[[int, int], None] | None = None) -> Matchups:
    """Scarica tutte le coppie campione/lane contro lane avversaria e le salva in data/matchups.json."""
    jobs = [(role, c, enemy_role) for role, stats in meta.lanes.items() for key, stat in stats.items()
            if stat.games >= MIN_CHAMP_GAMES and (c := db.by_key(key)) for enemy_role in ROLES]
    session = requests.Session()
    session.headers["User-Agent"] = "lolpick"
    data: dict = {}
    failed = done = 0

    def work(job: tuple) -> tuple[tuple, dict | None]:
        role, champ, enemy_role = job
        try:
            return job, _fetch_champion(session, meta.patch, role, champ, enemy_role)
        except (requests.RequestException, ValueError, KeyError):
            return job, None

    with ThreadPoolExecutor(WORKERS) as pool:
        for (role, champ, enemy_role), rows in pool.map(work, jobs):
            done += 1
            failed += rows is None
            if rows:
                data.setdefault(role, {}).setdefault(str(champ.key), {})[enemy_role] = rows
            if progress:
                progress(done, len(jobs))
    if not jobs or failed / len(jobs) > MAX_FAILED:
        raise ValueError(f"{failed} richieste fallite su {len(jobs)}")
    updated = time.strftime("%Y-%m-%d %H:%M")
    tmp = MATCHUPS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"patch": meta.patch, "updated": updated, "lanes": data}, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, MATCHUPS_FILE)
    return Matchups(meta.patch, data, updated, failed)


def load_matchups() -> Matchups | None:
    """Matchup salvati da `lolpick-update`; None se non sono mai stati scaricati."""
    try:
        raw = json.loads(MATCHUPS_FILE.read_text(encoding="utf-8"))
        return Matchups(raw["patch"], raw["lanes"], raw.get("updated", ""))
    except (OSError, ValueError, KeyError):
        return None
