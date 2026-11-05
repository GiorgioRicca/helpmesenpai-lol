"""Database dei campioni: voti dal CSV + nomi/id numerici da Data Dragon."""

from __future__ import annotations

import csv
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

import requests

from .criteria import BUILD_TYPES, KEYS, ROLES

PACKAGE_DATA = Path(__file__).resolve().parent / "data"
DATA_FILES = ["champions.csv", "ddragon_cache.json", "meta.json", "matchups.json", "laning.json"]


def _data_dir() -> Path:
    """Nel pacchetto; nell'eseguibile (PyInstaller) in data/ accanto al .exe, così resta modificabile e aggiornabile."""
    if not getattr(sys, "frozen", False):
        return PACKAGE_DATA
    data = Path(sys.executable).resolve().parent / "data"
    data.mkdir(exist_ok=True)
    for name in DATA_FILES:  # al primo avvio copia i dati inclusi nell'eseguibile
        if not (data / name).exists() and (PACKAGE_DATA / name).exists():
            shutil.copy2(PACKAGE_DATA / name, data / name)
    return data


DATA_DIR = _data_dir()
RATINGS_CSV = DATA_DIR / "champions.csv"
DDRAGON_CACHE = DATA_DIR / "ddragon_cache.json"
DDRAGON_URL = "https://ddragon.leagueoflegends.com"

# Profilo di ripiego per campioni nuovi non ancora presenti nel CSV.
TAG_PROFILES = {
    "Tank": {"tank": 8, "engage": 7, "cc": 7, "utility": 3, "sustain": 4},
    "Fighter": {"sustain": 6, "dps": 7, "splitpush": 6, "earlygame": 6, "tank": 5},
    "Assassin": {"burst": 8, "mobility": 8, "roaming": 7},
    "Mage": {"burst": 6, "poke": 6, "waveclear": 7, "cc": 5, "range": 7},
    "Marksman": {"dps": 9, "lategame": 8, "waveclear": 6, "poke": 4, "range": 6},
    "Support": {"utility": 8, "cc": 6, "disengage": 6},
}
TAG_BUILDS = {"Marksman": "crit", "Mage": "ap_burst", "Assassin": "lethality", "Fighter": "bruiser",
              "Tank": "tank", "Support": "enchanter"}
TAG_ROLES = {
    "Tank": ["top", "jungle", "utility"],
    "Fighter": ["top", "jungle"],
    "Assassin": ["middle", "jungle"],
    "Mage": ["middle"],
    "Marksman": ["bottom"],
    "Support": ["utility"],
}


@dataclass
class Champion:
    id: str  # id Data Dragon, es. "MonkeyKing"
    key: int  # id numerico usato dal client
    name: str  # nome visualizzato, es. "Wukong"
    roles: list[str]
    stats: dict[str, float]
    estimated: bool = False
    builds: list[str] = field(default_factory=list)  # archetipi di build, il primo è il più comune
    spells: tuple[int, ...] = ()  # summoner spell (id), note solo per gli alleati

    def __getitem__(self, criterion: str) -> float:
        return self.stats[criterion]


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _fetch_ddragon() -> dict:
    version = requests.get(f"{DDRAGON_URL}/api/versions.json", timeout=5).json()[0]
    url = f"{DDRAGON_URL}/cdn/{version}/data/en_US/champion.json"
    data = requests.get(url, timeout=10).json()["data"]
    champions = {
        c["id"]: {"key": int(c["key"]), "name": c["name"], "tags": c["tags"], "info": c["info"]}
        for c in data.values()
    }
    return {"version": version, "champions": champions}


def update_ddragon() -> dict:
    """Scarica la lista campioni dell'ultima patch e la salva in data/ddragon_cache.json."""
    dd = _fetch_ddragon()
    DDRAGON_CACHE.write_text(json.dumps(dd, ensure_ascii=False, indent=1), encoding="utf-8")
    return dd


def load_ddragon() -> dict:
    """Lista campioni salvata da `lolpick-update`; la scarica solo se manca del tutto."""
    if DDRAGON_CACHE.exists():
        return json.loads(DDRAGON_CACHE.read_text(encoding="utf-8"))
    try:
        return update_ddragon()
    except (requests.RequestException, ValueError, KeyError) as e:
        raise RuntimeError("Lista campioni assente e Data Dragon non raggiungibile: lancia `lolpick-update`.") from e


def load_ratings(path: Path = RATINGS_CSV) -> dict[str, dict]:
    """Voti per campione, indicizzati per nome normalizzato (vale sia l'id Data Dragon sia il nome, es. Wukong)."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except UnicodeDecodeError as e:
        raise SystemExit(f"{path} non è un CSV di testo (salvato come documento Numbers/Excel?). "
                         "Riesportalo come CSV UTF-8: in Numbers File > Esporta come > CSV.") from e
    # Numbers ed Excel in italiano esportano con ";" e a volte con il nome della tabella come prima riga.
    start = next((i for i, line in enumerate(lines) if line.startswith(("champion,", "champion;"))), None)
    if start is None:
        raise SystemExit(f"{path}: manca la riga di intestazione che inizia con \"champion\".")
    delimiter = ";" if lines[start].count(";") > lines[start].count(",") else ","
    rows = list(csv.DictReader(lines[start:], delimiter=delimiter))
    missing = [k for k in ["champion", "roles", *KEYS, "build"] if k not in rows[0]] if rows else []
    if missing:
        raise SystemExit(f"{path}: colonne mancanti {missing}.")
    ratings = {}
    for line_no, row in enumerate(rows, start=start + 2):
        if not row.get("champion"):
            continue  # righe vuote lasciate dal foglio di calcolo
        stats = {}
        for key in KEYS:
            value = float(row[key].replace(",", "."))
            if not 1 <= value <= 10:
                raise ValueError(f"{path.name}:{line_no}: {row['champion']}.{key}={value} fuori da 1-10")
            stats[key] = value
        roles = [r for r in row["roles"].split("|") if r]
        unknown = set(roles) - set(ROLES)
        if unknown:
            raise ValueError(f"{path.name}:{line_no}: ruoli sconosciuti {unknown}")
        builds = [b for b in row["build"].split("|") if b]
        unknown = set(builds) - BUILD_TYPES
        if not builds or unknown:
            raise ValueError(f"{path.name}:{line_no}: build non valida {row['build']!r}")
        ratings[_normalize(row["champion"])] = {"roles": roles, "stats": stats, "builds": builds,
                                                "estimated": bool(row.get("note"))}
    return ratings


def _estimate(dd: dict) -> tuple[list[str], dict[str, float], list[str]]:
    stats = {k: 4.0 for k in KEYS}
    roles: list[str] = []
    for tag in dd["tags"]:
        for k, v in TAG_PROFILES.get(tag, {}).items():
            stats[k] = max(stats[k], v)
        roles += [r for r in TAG_ROLES.get(tag, []) if r not in roles]
    stats["physical"] = float(min(10, max(1, dd["info"]["attack"])))
    stats["magic"] = float(min(10, max(1, dd["info"]["magic"])))
    builds = [TAG_BUILDS[t] for t in dd["tags"][:1] if t in TAG_BUILDS] or ["bruiser"]
    return roles or list(ROLES), stats, builds


class ChampionDB:
    def __init__(self, champions: list[Champion], version: str = ""):
        self.version = version
        self.all = sorted(champions, key=lambda c: c.name)
        self._by_key = {c.key: c for c in champions}
        self._by_name = {}
        for c in champions:
            self._by_name[_normalize(c.id)] = c
            self._by_name[_normalize(c.name)] = c

    def by_key(self, key: int) -> Champion | None:
        return self._by_key.get(key)

    def find(self, name: str) -> Champion | None:
        return self._by_name.get(_normalize(name))

    def for_role(self, role: str) -> list[Champion]:
        return [c for c in self.all if role in c.roles]

    @property
    def estimated(self) -> list[Champion]:
        return [c for c in self.all if c.estimated]


def load_champions() -> ChampionDB:
    dd = load_ddragon()
    ratings = load_ratings()
    champions = []
    for champ_id, info in dd["champions"].items():
        rating = ratings.get(_normalize(champ_id)) or ratings.get(_normalize(info["name"]))
        if rating:
            roles, stats, builds, estimated = rating["roles"], rating["stats"], rating["builds"], rating["estimated"]
        else:
            (roles, stats, builds), estimated = _estimate(info), True
        champions.append(Champion(champ_id, info["key"], info["name"], roles, stats, estimated, builds))
    return ChampionDB(champions, dd["version"])
