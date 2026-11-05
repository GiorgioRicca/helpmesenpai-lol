"""Champ select simulata, nello stesso formato della LCU, per provare l'app senza il client."""

from __future__ import annotations

import time

from .champions import ChampionDB
from .livegame import GameSnapshot, parse_live_game

ROLES = ["top", "jungle", "middle", "bottom", "utility"]
ME = 1  # cell id del giocatore locale (jungle, secondo pick della squadra)

# (tipo, cell, campione). Ordine standard: 6 ban, 6 pick, 4 ban, 4 pick.
SCRIPT = [
    ("ban", 0, "Kalista"), ("ban", 5, "Rell"), ("ban", 1, "Rumble"),
    ("ban", 6, "Ksante"), ("ban", 2, "Azir"), ("ban", 7, "Vi"),
    ("pick", 0, "Ornn"), ("pick", 5, "Jayce"), ("pick", 6, "Lee Sin"),
    ("pick", 1, "Viego"), ("pick", 2, "Ahri"), ("pick", 7, "Xerath"),
    ("ban", 8, "Varus"), ("ban", 3, "Caitlyn"), ("ban", 9, "Nautilus"), ("ban", 4, "Leona"),
    ("pick", 8, "Ezreal"), ("pick", 3, "Jinx"), ("pick", 4, "Thresh"), ("pick", 9, "Karma"),
]
HOVERS = {1: "Viego", 3: "Jinx", 4: "Thresh"}  # pick intent degli alleati prima del loro turno
# Summoner degli alleati (quelle nemiche il client non le mostra in champ select).
SPELLS = {0: (12, 4), 1: (11, 4), 2: (14, 4), 3: (7, 4), 4: (14, 4)}
# A partita iniziata: summoner nemiche, visibili solo ora.
ENEMY_SPELLS = {5: (12, 4), 6: (11, 4), 7: (21, 4), 8: (7, 4), 9: (14, 4)}
RAW_SPELL = {4: "SummonerFlash", 11: "SummonerSmite", 14: "SummonerDot", 12: "SummonerTeleport", 7: "SummonerHeal",
             21: "SummonerBarrier"}
LOADING_STEPS = 2  # passi tra la fine della champ select e l'inizio della partita


class DemoSource:
    def __init__(self, db: ChampionDB, step_seconds: float = 1.5):
        self.db = db
        self.step = step_seconds
        self.start = time.monotonic()

    def _key(self, name: str) -> int:
        return self.db.find(name).key

    def _steps(self) -> int:
        return int((time.monotonic() - self.start) / self.step)

    def get_game(self) -> dict | None:
        """Come la Live Client Data API: None finché la partita non è iniziata."""
        if self._steps() < len(SCRIPT) + LOADING_STEPS:
            return None
        picks = {cell: name for kind, cell, name in SCRIPT if kind == "pick"}

        def player(cell: int) -> dict:
            ally = cell < 5
            spells = (SPELLS if ally else ENEMY_SPELLS)[cell]
            champ = self.db.find(picks[cell])
            return {
                "riotId": f"Giocatore{cell}#DEMO",
                "championName": champ.name,
                "rawChampionName": f"game_character_displayname_{champ.id}",
                "team": "ORDER" if ally else "CHAOS",
                # Lasciamo vuote le posizioni nemiche per provare la deduzione (Smite + campioni).
                "position": ROLES[cell].upper() if ally else "",
                "summonerSpells": {
                    "summonerSpellOne": {"rawDisplayName": f"GeneratedTip_SummonerSpell_{RAW_SPELL[spells[0]]}_DisplayName"},
                    "summonerSpellTwo": {"rawDisplayName": f"GeneratedTip_SummonerSpell_{RAW_SPELL[spells[1]]}_DisplayName"},
                },
            }

        return {
            "activePlayer": {"riotId": f"Giocatore{ME}#DEMO"},
            "allPlayers": [player(c) for c in range(10)],
            "gameData": {"gameMode": "CLASSIC", "gameTime": 0.0},
        }

    def get_game_state(self, db: ChampionDB) -> GameSnapshot | None:
        game = self.get_game()
        return GameSnapshot("partita", game, parse_live_game(game, db)) if game else None

    def get_session(self) -> dict | None:
        steps = self._steps()
        if steps >= len(SCRIPT) + LOADING_STEPS // 2:
            return None  # champ select finita, la partita sta caricando
        done = min(len(SCRIPT), steps)
        finished = done == len(SCRIPT)
        actions, locked = [], {}
        for i, (kind, cell, name) in enumerate(SCRIPT):
            completed = i < done
            actions.append([{
                "actorCellId": cell, "type": kind, "completed": completed, "isInProgress": i == done,
                "championId": self._key(name) if completed else 0,
            }])
            if completed and kind == "pick":
                locked[cell] = self._key(name)

        def member(cell: int, ally: bool) -> dict:
            intent = self._key(HOVERS[cell]) if ally and cell in HOVERS and cell not in locked else 0
            return {
                "cellId": cell,
                "championId": locked.get(cell, 0),
                "championPickIntent": intent,
                "assignedPosition": ROLES[cell] if ally else "",
                "spell1Id": SPELLS[cell][0] if ally else 0,
                "spell2Id": SPELLS[cell][1] if ally else 0,
            }

        return {
            "localPlayerCellId": ME,
            "actions": actions,
            "myTeam": [member(c, True) for c in range(5)],
            "theirTeam": [member(c, False) for c in range(5, 10)],
            "bans": {"myTeamBans": [], "theirTeamBans": []},
            "timer": {
                "phase": "FINALIZATION" if finished else "BAN_PICK",
                "adjustedTimeLeftInPhase": int(self.step * 1000),
            },
        }
