"""Analisi lane per lane. La bot lane è un'unica lane 2v2 (ADC + support)."""

from __future__ import annotations

from dataclasses import dataclass

from .champions import Champion
from .draft import DraftState, Slot
from .laning import Laning
from .matchups import GUESSED_ROLE

LANES = [("top", ["top"]), ("jungle", ["jungle"]), ("middle", ["middle"]), ("bot", ["bottom", "utility"])]
LANE_LABELS = {"top": "Top", "jungle": "Jungle", "middle": "Mid", "bot": "Bot (ADC + Support)"}
EDGE = 0.8
PRIO_EDGE = 0.6
GOLD_PER_PRIO = 250  # oro di vantaggio al minuto 15 che vale un punto di priorità: i dati pesano più dei voti


def _mean(champions: list[Champion], formula) -> float:
    return sum(formula(c) for c in champions) / len(champions)


def lane_pressure(champions: list[Champion]) -> float:
    """Forza nella fase di lane: early game soprattutto, poi kill pressure e trade."""
    return _mean(champions, lambda c: 0.45 * c["earlygame"] + 0.2 * c["burst"] + 0.15 * c["poke"]
                 + 0.1 * c["sustain"] + 0.1 * c["cc"])


def lane_priority(champions: list[Champion]) -> float:
    """Chi spinge per primo e si muove per primo: forza early, waveclear, CC per vincere i trade, range."""
    return _mean(champions, lambda c: 0.4 * c["earlygame"] + 0.3 * c["waveclear"] + 0.15 * c["cc"] + 0.15 * c["range"])


def gank_setup(champions: list[Champion]) -> float:
    """Quanto i campioni in lane aiutano il loro jungler a chiudere un gank."""
    return _mean(champions, lambda c: 0.45 * c["cc"] + 0.3 * c["earlygame"] + 0.25 * c["burst"])


def vulnerability(champions: list[Champion]) -> float:
    """Quanto è facile uccidere questi campioni in un gank: pochi escape, fragili, niente disengage."""
    return _mean(champions, lambda c: 0.5 * (10 - c["mobility"]) + 0.3 * (10 - c["tank"]) + 0.2 * (10 - c["disengage"]))


@dataclass
class LaneMatchup:
    lane: str
    ours: list[Champion]
    theirs: list[Champion]
    our_pressure: float
    their_pressure: float
    gank_for_us: float | None  # facilità per il nostro jungler di gankare qui (0-10)
    gank_for_them: float | None  # facilità per il loro jungler di gankare noi qui (0-10)
    inferred: bool  # ruoli nemici dedotti, non certi
    our_prio: float = 0.0
    their_prio: float = 0.0
    gd15: float | None = None  # nostro distacco d'oro atteso al minuto 15 nella lane; già compreso nella priorità

    @property
    def label(self) -> str:
        return LANE_LABELS[self.lane]

    @property
    def diff(self) -> float:
        return self.our_pressure - self.their_pressure

    @property
    def prio(self) -> str:
        """Chi ha la priorità sull'onda: "noi", "loro" o "contesa"."""
        diff = self.our_prio - self.their_prio
        return "noi" if diff >= PRIO_EDGE else "loro" if diff <= -PRIO_EDGE else "contesa"

    @property
    def verdict(self) -> str:
        if self.diff >= EDGE:
            return "favorevole"
        if self.diff <= -EDGE:
            return "sfavorevole"
        return "equilibrata"


def _lane_slots(slots: list[Slot], roles: list[str]) -> list[Slot]:
    return [s for r in roles for s in slots if s.role == r and s.champion]


def _lane_gold(ours: list[Slot], theirs: list[Slot], roles: list[str], laning: Laning | None) -> float | None:
    """Distacco atteso di tutta la lane (bot: ADC più support); None se mancano i dati."""
    edges = [laning.edge(o.champion, t.champion, r) for o, t, r in zip(ours, theirs, roles)] if laning else []
    if not edges or None in edges:
        return None
    return sum(edges) * (GUESSED_ROLE if any(t.role_inferred for t in theirs) else 1)


def lane_matchups(state: DraftState, laning: Laning | None = None) -> list[LaneMatchup]:
    out = []
    for lane, roles in LANES:
        ours, theirs = _lane_slots(state.allies, roles), _lane_slots(state.enemies, roles)
        if len(ours) != len(roles) or len(theirs) != len(roles):
            continue
        our_champs, their_champs = [s.champion for s in ours], [s.champion for s in theirs]
        gankable = lane != "jungle"
        gold = _lane_gold(ours, theirs, roles, laning) if gankable else None
        swing = (gold or 0.0) / GOLD_PER_PRIO / 2  # metà a ciascuno: lo scarto tra le due squadre cambia di gold/GOLD_PER_PRIO
        out.append(LaneMatchup(
            lane=lane,
            ours=our_champs,
            theirs=their_champs,
            our_pressure=lane_pressure(our_champs),
            their_pressure=lane_pressure(their_champs),
            gank_for_us=0.5 * gank_setup(our_champs) + 0.5 * vulnerability(their_champs) if gankable else None,
            gank_for_them=0.5 * gank_setup(their_champs) + 0.5 * vulnerability(our_champs) if gankable else None,
            inferred=any(s.role_inferred for s in theirs),
            our_prio=lane_priority(our_champs) + swing,
            their_prio=lane_priority(their_champs) - swing,
            gd15=gold,
        ))
    return out


def best_gank(lanes: list[LaneMatchup]) -> LaneMatchup | None:
    candidates = [l for l in lanes if l.gank_for_us is not None]
    return max(candidates, key=lambda l: l.gank_for_us, default=None)


def most_exposed(lanes: list[LaneMatchup]) -> LaneMatchup | None:
    candidates = [l for l in lanes if l.gank_for_them is not None]
    return max(candidates, key=lambda l: l.gank_for_them, default=None)
