"""Stato della champ select ricavato dalla sessione LCU."""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import permutations

from .champions import Champion, ChampionDB
from .criteria import ROLES
from .summoners import with_spells


@dataclass
class Slot:
    cell_id: int
    role: str = ""  # ruolo assegnato (o dedotto per i nemici); "" se sconosciuto
    champion: Champion | None = None  # campione lockato
    hover: Champion | None = None  # campione selezionato ma non ancora lockato
    is_me: bool = False
    role_inferred: bool = False

    @property
    def locked(self) -> bool:
        return self.champion is not None


@dataclass
class DraftState:
    allies: list[Slot]
    enemies: list[Slot]
    bans: set[int] = field(default_factory=set)
    phase: str = ""
    time_left: float | None = None

    @property
    def me(self) -> Slot | None:
        return next((s for s in self.allies if s.is_me), None)

    @property
    def ally_champions(self) -> list[Champion]:
        return [s.champion for s in self.allies if s.champion]

    @property
    def enemy_champions(self) -> list[Champion]:
        return [s.champion for s in self.enemies if s.champion]

    @property
    def all_locked(self) -> bool:
        slots = self.allies + self.enemies
        return bool(self.allies) and bool(self.enemies) and all(s.locked for s in slots)

    @property
    def opponents(self) -> list[tuple[Champion, str, bool]]:
        """Nemici lockati con il ruolo e se è certo; i ruoli mancanti (champ select) si deducono dai campioni."""
        known = [s for s in self.enemies if s.champion and s.role]
        unknown = [s.champion for s in self.enemies if s.champion and not s.role]
        free = [r for r in ROLES if r not in {s.role for s in known}]
        guessed = infer_roles(unknown, free) if len(unknown) <= len(free) else []
        return [(s.champion, s.role, not s.role_inferred) for s in known] + [(c, r, False) for c, r in zip(unknown, guessed)]

    def unavailable(self, except_slot: Slot | None = None) -> set[int]:
        """Campioni che non si possono più suggerire: bannati, lockati o in hover dagli alleati."""
        taken = set(self.bans)
        for s in self.allies + self.enemies:
            if s.champion:
                taken.add(s.champion.key)
            if s.hover and s is not except_slot and s in self.allies:
                taken.add(s.hover.key)
        return taken

    def key(self) -> tuple:
        return tuple(s.champion.key if s.champion else 0 for s in self.allies + self.enemies)


def _role_score(champ: Champion, role: str) -> int:
    if role not in champ.roles:
        return 0
    return 3 if champ.roles[0] == role else 2


def infer_roles(champions: list[Champion], free_roles: list[str] = ROLES) -> list[str]:
    """Assegna a ogni campione il ruolo più plausibile, senza ripetizioni."""
    if not champions:
        return []
    best, best_score = None, -1
    for perm in permutations(free_roles, len(champions)):
        score = sum(_role_score(c, r) for c, r in zip(champions, perm))
        if score > best_score:
            best, best_score = perm, score
    return list(best) if best else [""] * len(champions)


SMITE = 11


def assign_roles_from_order(team: list[Slot]) -> bool:
    """Schermata di caricamento: i giocatori compaiono in ordine top, jungle, mid, adc, support.

    Vale solo se nessun ruolo è già noto e l'ordine è coerente (chi ha Smite è il secondo).
    """
    if len(team) != len(ROLES) or any(s.role for s in team):
        return False
    smiters = [i for i, s in enumerate(team) if SMITE in s.champion.spells]
    if smiters and smiters != [ROLES.index("jungle")]:
        return False
    for slot, role in zip(team, ROLES):
        slot.role = role
    return True


def assign_missing_roles(team: list[Slot]) -> None:
    """Completa i ruoli mancanti: chi ha Smite è il jungler, gli altri si deducono dai campioni."""
    unknown = [s for s in team if not s.role and s.champion]
    free = [r for r in ROLES if r not in {s.role for s in team}]
    smiters = [s for s in unknown if SMITE in s.champion.spells]
    if len(smiters) == 1 and "jungle" in free:
        smiters[0].role, smiters[0].role_inferred = "jungle", True
        unknown.remove(smiters[0])
        free.remove("jungle")
    if unknown and len(unknown) <= len(free):
        for slot, role in zip(unknown, infer_roles([s.champion for s in unknown], free)):
            slot.role, slot.role_inferred = role, True


def parse_session(session: dict, db: ChampionDB) -> DraftState:
    me_cell = session.get("localPlayerCellId")
    locked: dict[int, int] = {}
    pick_cells: set[int] = set()
    bans: set[int] = set()

    for turn in session.get("actions", []):
        for action in turn:
            champ_id = action.get("championId") or 0
            if action.get("type") == "pick":
                pick_cells.add(action.get("actorCellId"))
                if action.get("completed") and champ_id:
                    locked[action["actorCellId"]] = champ_id
            elif action.get("type") == "ban" and action.get("completed") and champ_id:
                bans.add(champ_id)
    session_bans = session.get("bans") or {}
    bans |= {b for b in session_bans.get("myTeamBans", []) + session_bans.get("theirTeamBans", []) if b}

    timer = session.get("timer") or {}
    phase = timer.get("phase", "")
    time_left = timer.get("adjustedTimeLeftInPhase")

    def make_slot(member: dict, ally: bool) -> Slot:
        cell = member.get("cellId")
        champ_id = member.get("championId") or 0
        intent = member.get("championPickIntent") or 0
        if cell in locked:
            locked_id = locked[cell]
        elif not pick_cells or phase == "FINALIZATION":
            # Nessuna azione di pick (custom) o draft concluso: vale il campione mostrato.
            locked_id = champ_id
        else:
            locked_id = 0
        hover_id = 0 if locked_id else (champ_id or intent)
        # Le summoner nemiche in champ select arrivano a 0 (nascoste): restano senza modifiche.
        spells = [member.get("spell1Id") or 0, member.get("spell2Id") or 0]

        def champion(key: int) -> Champion | None:
            champ = db.by_key(key) if key else None
            return with_spells(champ, spells) if champ else None

        return Slot(
            cell_id=cell,
            role=(member.get("assignedPosition") or "").lower(),
            champion=champion(locked_id),
            hover=champion(hover_id),
            is_me=ally and cell == me_cell,
        )

    allies = [make_slot(m, True) for m in session.get("myTeam", [])]
    enemies = [make_slot(m, False) for m in session.get("theirTeam", [])]
    # In champ select ruoli e summoner nemici sono nascosti: si conoscono solo a partita iniziata.
    # Gli alleati il client li ordina per turno di pick: li mostriamo da Top a Support.
    allies.sort(key=lambda s: ROLES.index(s.role) if s.role in ROLES else len(ROLES))
    return DraftState(allies, enemies, bans, phase, time_left / 1000 if time_left else None)
