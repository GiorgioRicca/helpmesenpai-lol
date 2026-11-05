"""Fase di lane della patch: distacco d'oro al minuto 15 (GD15) per ogni matchup nella stessa lane, in Diamond+.

Fonte: statistiche di U.GG, scaricate da `lolpick-update` in data/laning.json: a runtime l'app legge solo quel file.
La "percentuale di lane vinte" non è pubblicata da nessuna fonte trovata: GD15 è la misura più vicina.

Nei file di U.GG ogni riga è dal punto di vista dell'avversario (verificato sulle pagine "counter" del sito),
quindi qui si inverte il segno. Con poche partite il distacco contro un avversario tende alla media del campione.
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
from .matchups import GUESSED_ROLE, MAX_FAILED, MIN_ROW_GAMES, WORKERS

LANING_FILE = DATA_DIR / "laning.json"
VERSIONS_URL = "https://static.bigbrain.gg/assets/lol/riot_patch_update/prod/ugg/ugg-api-versions.json"
MATCHUPS_URL = "https://stats2.u.gg/lol/{version}/matchups/{patch}/ranked_solo_5x5/{key}/{data}.json"
WORLD, DIAMOND_PLUS = "12", "11"  # identificativi di U.GG
ROLE_IDS = {"jungle": "1", "utility": "2", "bottom": "3", "top": "4", "middle": "5"}
WINS, GAMES, GOLD = 1, 2, 4  # colonne delle righe di U.GG
MIN_LANE_GAMES = 200  # sotto queste partite il campione in quella lane è solo rumore
PRIOR_GAMES = 50  # smussamento stimato dai dati: rumore ~2500 oro a partita, differenze reali tra matchup ~350


@dataclass
class LaneStat:
    role: str
    games: int
    gold: float  # GD15 medio del campione nella lane
    estimate: float  # GD15 atteso in questa draft: contro l'avversario di lane se noto, altrimenti la media
    enemy: Champion | None = None
    enemy_games: int = 0


class Laning:
    def __init__(self, patch: str, data: dict, updated: str = "", failed: int = 0):
        self.patch = patch
        self.data = data  # {ruolo: {id campione: {"games": n, "gold": media, "vs": {id avversario: [partite, GD15]}}}}
        self.updated = updated
        self.failed = failed  # richieste non riuscite nell'ultimo download

    def versus(self, champion: Champion, role: str, opponents: list[tuple[Champion, str, bool]]) -> LaneStat | None:
        """GD15 atteso nella lane `role`, dati i nemici lockati (campione, ruolo, ruolo certo)."""
        entry = self.data.get(role, {}).get(str(champion.key))
        if not entry:
            return None
        stat = LaneStat(role, entry["games"], entry["gold"], entry["gold"])
        for enemy, enemy_role, certain in opponents:
            row = entry["vs"].get(str(enemy.key)) if enemy_role == role else None
            if row:
                games, gold = row
                pull = games / (games + PRIOR_GAMES) * (1 if certain else GUESSED_ROLE)
                stat.estimate = entry["gold"] + (gold - entry["gold"]) * pull
                stat.enemy, stat.enemy_games = enemy, games
        return stat


    def edge(self, champion: Champion, enemy: Champion, role: str) -> float | None:
        """GD15 atteso del campione sull'avversario diretto: con poche partite tende alla differenza tra le loro medie."""
        lane = self.data.get(role, {})
        ours, theirs = lane.get(str(champion.key)), lane.get(str(enemy.key))
        row = ours["vs"].get(str(enemy.key)) if ours else None
        if ours and theirs:
            prior = ours["gold"] - theirs["gold"]
            return prior + (row[1] - prior) * row[0] / (row[0] + PRIOR_GAMES) if row else prior
        return row[1] if row else None


def _parse(raw: dict) -> dict:
    out = {}
    for role, role_id in ROLE_IDS.items():
        rows = raw.get(WORLD, {}).get(DIAMOND_PLUS, {}).get(role_id)
        rows = rows[0] if rows else []
        games = sum(r[GAMES] for r in rows)
        if games >= MIN_LANE_GAMES:
            out[role] = {"games": games, "gold": round(-sum(r[GOLD] for r in rows) / games, 1),
                         "vs": {str(r[0]): [r[GAMES], round(-r[GOLD] / r[GAMES], 1)] for r in rows if r[GAMES] >= MIN_ROW_GAMES}}
    return out


def _fetch_champion(session: requests.Session, url: str) -> dict:
    for attempt in range(4):
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 404:  # campione appena uscito, senza dati per questa patch
                return {}
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return _parse(r.json())
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    return {}


def update_laning(db: ChampionDB, patch: str, progress: Callable[[int, int], None] | None = None) -> Laning:
    """Scarica i matchup di ogni campione e salva il GD15 per lane in data/laning.json."""
    session = requests.Session()
    session.headers["User-Agent"] = "lolpick"
    ugg_patch = patch.replace(".", "_")
    data_version = session.get(VERSIONS_URL, timeout=20).json()[ugg_patch]["matchups"]
    version = ".".join(data_version.split(".")[:2])
    data: dict = {}
    failed = done = 0

    def work(champ: Champion) -> tuple[Champion, dict | None]:
        url = MATCHUPS_URL.format(version=version, patch=ugg_patch, key=champ.key, data=data_version)
        try:
            return champ, _fetch_champion(session, url)
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
            return champ, None

    with ThreadPoolExecutor(WORKERS) as pool:
        for champ, roles in pool.map(work, db.all):
            done += 1
            failed += roles is None
            for role, entry in (roles or {}).items():
                data.setdefault(role, {})[str(champ.key)] = entry
            if progress:
                progress(done, len(db.all))
    if failed / len(db.all) > MAX_FAILED or not data:
        raise ValueError(f"{failed} richieste fallite su {len(db.all)}")
    updated = time.strftime("%Y-%m-%d %H:%M")
    tmp = LANING_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"patch": patch, "updated": updated, "lanes": data}, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, LANING_FILE)
    return Laning(patch, data, updated, failed)


def load_laning() -> Laning | None:
    """GD15 salvato da `lolpick-update`; None se non è mai stato scaricato."""
    try:
        raw = json.loads(LANING_FILE.read_text(encoding="utf-8"))
        return Laning(raw["patch"], raw["lanes"], raw.get("updated", ""))
    except (OSError, ValueError, KeyError):
        return None
