"""Tier list della patch: forza nel meta di ogni campione per lane, da pick rate e win rate in Diamond+.

Fonte: statistiche aggregate per lane di Lolalytics (Diamond+, ranked solo/duo, tutte le regioni),
scaricate da `lolpick-update` in data/meta.json: a runtime l'app legge solo quel file.

Algoritmo, per ogni lane:
1. win rate "smussato": (vittorie + K·media) / (partite + K), cioè con poche partite il win rate
   tende alla media della lane invece di fidarsi di un 60% su 200 game;
2. punteggio = (win rate smussato − media della lane) in punti + PICK_WEIGHT · log2(pick rate):
   ogni raddoppio del pick rate vale come PICK_WEIGHT punti di win rate (tanti pick = forte e affidabile);
3. z-score del punteggio rispetto ai campioni giocati davvero in quella lane → tier S+ … D.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from statistics import mean, pstdev

import requests

from .champions import DATA_DIR, Champion
from .criteria import ROLES

META_FILE = DATA_DIR / "meta.json"
META_URL = "https://a1.lolalytics.com/mega/"
LANE_NAMES = {"top": "top", "jungle": "jungle", "middle": "middle", "bottom": "bottom", "utility": "support"}
ELO = "diamond_plus"

PRIOR_GAMES = 3000  # partite "virtuali" alla media della lane nello smussamento
PICK_WEIGHT = 0.75  # punti di win rate equivalenti a un raddoppio del pick rate
VIABLE_PICK = 0.5  # % di pick rate sotto cui il campione non conta per media e deviazione della lane
MIN_LANE_GAMES = 150_000  # sotto questa soglia (inizio patch) si usa la patch precedente
TIERS = [(1.5, "S+"), (0.8, "S"), (0.25, "A"), (-0.4, "B"), (-1.0, "C")]  # soglie di z-score, sotto: D


@dataclass
class MetaStat:
    role: str
    games: int
    winrate: float  # % grezza
    pickrate: float  # %
    delta: float  # win rate smussato − media della lane, in punti
    z: float  # forza nel meta rispetto agli altri campioni della lane

    @property
    def tier(self) -> str:
        return next((name for limit, name in TIERS if self.z >= limit), "D")

    def describe(self) -> str:
        return f"tier {self.tier} ({self.delta:+.1f}% WR, {self.pickrate:.1f}% pick)"


class Meta:
    def __init__(self, patch: str, lanes: dict[str, dict[int, MetaStat]], updated: str = ""):
        self.patch = patch
        self.lanes = lanes
        self.updated = updated

    def age_days(self) -> int:
        try:
            return int((time.time() - time.mktime(time.strptime(self.updated, "%Y-%m-%d %H:%M"))) // 86400)
        except ValueError:
            return 0

    def get(self, champion: Champion, role: str) -> MetaStat | None:
        return self.lanes.get(role, {}).get(champion.key)

    def best(self, champion: Champion, roles: list[str]) -> MetaStat | None:
        """La lane, tra quelle possibili, in cui il campione è più forte."""
        stats = [s for r in roles if (s := self.get(champion, r))]
        return max(stats, key=lambda s: s.z, default=None)

    def for_role(self, champion: Champion, role: str) -> MetaStat | None:
        """Tier nel ruolo assegnato; se il ruolo non è noto (nemici in champ select), nei suoi ruoli abituali."""
        return self.get(champion, role) if role else self.best(champion, champion.roles)


def _score_lane(role: str, rows: list[list[float]]) -> dict[int, MetaStat]:
    """rows: [id campione, partite, win rate %, pick rate %]."""
    rows = [r for r in rows if r[1] > 0]
    games = sum(r[1] for r in rows)
    if not games:
        return {}
    avg = sum(r[1] * r[2] for r in rows) / games  # media pesata della lane (assorbe l'offset della fonte)
    raw = {}
    for key, g, wr, pr in rows:
        smoothed = (g * wr + PRIOR_GAMES * avg) / (g + PRIOR_GAMES)
        raw[int(key)] = (g, wr, pr, smoothed - avg, smoothed - avg + PICK_WEIGHT * math.log2(max(pr, 0.05)))
    viable = [v[4] for v in raw.values() if v[2] >= VIABLE_PICK] or [v[4] for v in raw.values()]
    mu, sigma = mean(viable), pstdev(viable) or 1.0
    return {key: MetaStat(role, int(g), wr, pr, delta, (score - mu) / sigma)
            for key, (g, wr, pr, delta, score) in raw.items()}


def _fetch_lane(patch: str, lane: str) -> list[list[float]]:
    params = {"ep": "list", "v": 1, "patch": patch, "lane": lane, "tier": ELO, "queue": "ranked", "region": "all"}
    r = requests.get(META_URL, params=params, timeout=10, headers={"User-Agent": "lolpick"})
    r.raise_for_status()
    return [[int(k), v.get("games") or 0, v.get("wr") or 0, v.get("pr") or 0] for k, v in r.json().get("cid", {}).items()]


def _previous(patch: str) -> str | None:
    major, minor = patch.split(".")
    return f"{major}.{int(minor) - 1}" if int(minor) > 1 else None


def _fetch(patch: str) -> dict:
    """Dati grezzi di tutte le lane; a inizio patch, con poche partite, ripiega sulla precedente."""
    lanes = {role: _fetch_lane(patch, lane) for role, lane in LANE_NAMES.items()}
    thin = min(sum(r[1] for r in rows) for rows in lanes.values()) < MIN_LANE_GAMES
    prev = _previous(patch)
    if thin and prev:
        return _fetch(prev)
    return {"patch": patch, "lanes": lanes}


def update_meta(version: str) -> Meta:
    """Scarica la tier list della patch (versione Data Dragon, es. 16.21.1) e la salva in data/meta.json."""
    patch = ".".join(version.split(".")[:2])
    data = {"for": patch, "updated": time.strftime("%Y-%m-%d %H:%M"), **_fetch(patch)}
    META_FILE.write_text(json.dumps(data), encoding="utf-8")
    return _build(data)


def _build(data: dict) -> Meta:
    lanes = {role: _score_lane(role, rows) for role, rows in data["lanes"].items() if role in ROLES}
    return Meta(data["patch"], lanes, data.get("updated", ""))


def load_meta() -> Meta | None:
    """Tier list salvata da `lolpick-update`; None se non è mai stata scaricata."""
    try:
        return _build(json.loads(META_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError):
        return None
