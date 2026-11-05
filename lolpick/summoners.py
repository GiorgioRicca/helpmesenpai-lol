"""Summoner spell e il loro effetto sui criteri del campione che le porta."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from .champions import Champion


@dataclass(frozen=True)
class Spell:
    id: int
    name: str
    aliases: tuple[str, ...]
    modifiers: dict[str, float] = field(default_factory=dict)


SPELLS = {s.id: s for s in [
    Spell(4, "Flash", ("flash",)),
    Spell(11, "Smite", ("smite", "punizione")),
    Spell(14, "Ignite", ("ignite", "ustione", "incendio", "dot"), {"earlygame": 1.5, "burst": 0.5}),
    Spell(3, "Exhaust", ("exhaust", "exh", "sfinimento"), {"disengage": 1.5}),
    Spell(12, "Teleport", ("teleport", "tp", "teletrasporto"), {"splitpush": 1.0, "roaming": 1.0}),
    Spell(6, "Ghost", ("ghost", "spettralita", "spettralità", "haste"), {"mobility": 1.0, "engage": 1.0}),
    Spell(7, "Heal", ("heal", "guarigione"), {"sustain": 1.0}),
    Spell(21, "Barrier", ("barrier", "barriera"), {"sustain": 0.5, "tank": 0.5}),
    Spell(1, "Cleanse", ("cleanse", "purificazione"), {"utility": 0.5}),
]}

_BY_ALIAS = {alias: s for s in SPELLS.values() for alias in s.aliases}


def find_spell(name: str) -> Spell | None:
    return _BY_ALIAS.get(re.sub(r"[^a-zà]", "", name.lower()))


def with_spells(champion: Champion, spell_ids: list[int]) -> Champion:
    """Copia del campione con i voti corretti dalle summoner (max 10)."""
    spells = tuple(i for i in spell_ids if i in SPELLS)
    if not spells:
        return champion
    stats = dict(champion.stats)
    for spell_id in spells:
        for k, v in SPELLS[spell_id].modifiers.items():
            stats[k] = min(10.0, stats[k] + v)
    return replace(champion, stats=stats, spells=spells)


def spell_names(champion: Champion) -> str:
    return "+".join(SPELLS[i].name for i in champion.spells)
