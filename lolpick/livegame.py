"""Ruoli e summoner di tutti e dieci i giocatori, noti solo da quando parte la schermata di caricamento.

Due fonti, entrambe in sola lettura:
- client (LCU) `/lol-gameflow/v1/session`: disponibile già nella schermata di caricamento;
- Live Client Data API ufficiale (https://127.0.0.1:2999): disponibile a partita iniziata.

Ruoli, in ordine di affidabilità: posizione esplicita, ordine della schermata di caricamento
(top, jungle, mid, adc, support), deduzione da Smite e campioni.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests
import urllib3

from .champions import Champion, ChampionDB
from .criteria import ROLES
from .draft import DraftState, Slot, assign_missing_roles, assign_roles_from_order
from .summoners import with_spells

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

LIVE_URL = "https://127.0.0.1:2999/liveclientdata/allgamedata"
POSITIONS = {"TOP": "top", "JUNGLE": "jungle", "MIDDLE": "middle", "BOTTOM": "bottom", "UTILITY": "utility"}
# Id Data Dragon delle summoner; nel gioco compaiono dentro rawDisplayName (anche in varianti come la Smite potenziata).
SPELL_KEYS = {"SummonerFlash": 4, "SummonerSmite": 11, "SummonerDot": 14, "SummonerExhaust": 3, "SummonerTeleport": 12,
              "SummonerHaste": 6, "SummonerHeal": 7, "SummonerBarrier": 21, "SummonerBoost": 1}
CHAMPION_PREFIX = "game_character_displayname_"
LOADING_PHASES = {"GameStart", "InProgress", "Reconnect"}


@dataclass
class GameSnapshot:
    source: str  # "caricamento" (LCU) o "partita" (Live Client Data)
    raw: dict  # risposta originale, salvata per verificare i campi disponibili
    state: DraftState | None  # None se non è Summoner's Rift

    def key(self) -> frozenset[int]:
        return frozenset(s.champion.key for s in self.state.allies + self.state.enemies) if self.state else frozenset()


def _finish(allies: list[Slot], enemies: list[Slot], phase: str) -> DraftState:
    for team in (allies, enemies):
        if not assign_roles_from_order(team):
            assign_missing_roles(team)
        team.sort(key=lambda s: ROLES.index(s.role) if s.role in ROLES else len(ROLES))
    return DraftState(allies, enemies, phase=phase)


# --- Live Client Data API (partita iniziata) ---

def _spell_id(spell: dict | None) -> int:
    raw = (spell or {}).get("rawDisplayName", "")
    return next((key for name, key in SPELL_KEYS.items() if name in raw), 0)


def _player_id(player: dict) -> str:
    return player.get("riotId") or player.get("summonerName") or ""


def parse_live_game(data: dict, db: ChampionDB) -> DraftState | None:
    if data.get("gameData", {}).get("gameMode") != "CLASSIC":
        return None
    me_id = _player_id(data.get("activePlayer", {}))
    players = data["allPlayers"]
    my_team = next((p["team"] for p in players if me_id and _player_id(p) == me_id), "ORDER")

    allies, enemies = [], []
    for i, p in enumerate(players):  # stesso ordine della schermata di caricamento
        raw = p.get("rawChampionName", "")
        champ = db.find(raw.removeprefix(CHAMPION_PREFIX)) or db.find(p.get("championName", ""))
        if not champ:
            continue
        spells = p.get("summonerSpells", {})
        champ = with_spells(champ, [_spell_id(spells.get("summonerSpellOne")), _spell_id(spells.get("summonerSpellTwo"))])
        slot = Slot(i, POSITIONS.get(p.get("position", ""), ""), champ, is_me=bool(me_id) and _player_id(p) == me_id)
        (allies if p.get("team") == my_team else enemies).append(slot)
    return _finish(allies, enemies, "PARTITA")


class LiveGameSource:
    def get_game_state(self, db: ChampionDB) -> GameSnapshot | None:
        try:
            r = requests.get(LIVE_URL, timeout=1, verify=False)
        except requests.RequestException:
            return None  # nessuna partita in corso (o ancora in caricamento)
        if r.status_code != 200:
            return None
        data = r.json()
        return GameSnapshot("partita", data, parse_live_game(data, db)) if data.get("allPlayers") else None


# --- LCU gameflow (schermata di caricamento) ---

def parse_gameflow(session: dict, db: ChampionDB, my_puuid: str) -> DraftState | None:
    game = session.get("gameData") or {}
    mode = (game.get("queue") or {}).get("gameMode") or (session.get("map") or {}).get("gameMode")
    if mode != "CLASSIC":
        return None
    spells = {p.get("championId"): [p.get("spell1Id") or 0, p.get("spell2Id") or 0]
              for p in game.get("playerChampionSelections") or []}
    teams = [game.get("teamOne") or [], game.get("teamTwo") or []]
    mine = next((i for i, t in enumerate(teams) if my_puuid and any(p.get("puuid") == my_puuid for p in t)), 0)

    def slots(players: list[dict], offset: int) -> list[Slot]:
        out = []
        for i, p in enumerate(players):
            champ: Champion | None = db.by_key(p.get("championId") or 0)
            if not champ:
                continue
            champ = with_spells(champ, spells.get(champ.key, []))
            role = POSITIONS.get((p.get("selectedPosition") or "").upper(), "")
            out.append(Slot(offset + i, role, champ, is_me=bool(my_puuid) and p.get("puuid") == my_puuid))
        return out

    return _finish(slots(teams[mine], 0), slots(teams[1 - mine], 5), "CARICAMENTO")


def gameflow_snapshot(session: dict | None, db: ChampionDB, my_puuid: str) -> GameSnapshot | None:
    if not session or session.get("phase") not in LOADING_PHASES:
        return None
    game = session.get("gameData") or {}
    if not (game.get("teamOne") and game.get("teamTwo")):
        return None
    return GameSnapshot("caricamento", session, parse_gameflow(session, db, my_puuid))
