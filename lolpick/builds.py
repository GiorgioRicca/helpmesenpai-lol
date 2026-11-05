"""Archetipo di build consigliato e adattamenti alla partita (senza nomi di oggetti, che cambiano a ogni patch)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .analysis import Report, game_tempo, physical_share, team_profile
from .champions import Champion
from .criteria import BUILDS

AGAINST_TANKS = {"onhit", "bruiser", "ap_dps", "ap_bruiser"}  # danno sostenuto / %vita
AGAINST_SQUISHIES = {"lethality", "ap_burst", "crit"}
FRAGILE = {"crit", "onhit", "lethality", "ap_burst", "ap_dps", "ad_caster", "enchanter"}
MAGIC = {"ap_burst", "ap_dps", "ap_bruiser"}


@dataclass
class BuildAdvice:
    archetype: str
    reason: str = ""
    adaptations: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return BUILDS[self.archetype][0]

    @property
    def stats(self) -> str:
        return BUILDS[self.archetype][1]


def _names(champions: list[Champion]) -> str:
    return ", ".join(c.name for c in champions)


def _choose(me: Champion, allies: list[Champion], enemies: list[Champion]) -> tuple[str, str]:
    options = me.builds
    if len(options) == 1 or not enemies:
        return options[0], ""
    theirs = team_profile(enemies)
    others = [c for c in allies if c.key != me.key]
    if "tank" in options and others and team_profile(others)["tank"] < 5:
        return "tank", "manca una frontline: tocca a te"
    if theirs["tank"] >= 5.5:
        pick = next((o for o in options if o in AGAINST_TANKS), None)
        if pick:
            return pick, "contro i loro tank serve danno sostenuto"
    if theirs["tank"] <= 3.5:
        pick = next((o for o in options if o in AGAINST_SQUISHIES), None)
        if pick:
            return pick, "sono fragili: punta sul danno"
    return options[0], ""


def recommend_build(me: Champion, allies: list[Champion], enemies: list[Champion], report: Report) -> BuildAdvice:
    archetype, reason = _choose(me, allies, enemies)
    advice = BuildAdvice(archetype, reason)
    if not enemies:
        return advice
    theirs, out = report.theirs, advice.adaptations

    share = physical_share(enemies)
    if share >= 0.6:
        out.append(f"Difesa: soprattutto armatura (loro {share:.0%} danni fisici)")
    elif share <= 0.4:
        out.append(f"Difesa: soprattutto resistenza magica (loro {1 - share:.0%} danni magici)")
    else:
        out.append("Difesa: danni misti, vita e resistenze bilanciate")

    healers = [c for c in enemies if c["sustain"] >= 8]
    if healers or theirs["sustain"] >= 5.5:
        out.append(f"Anti-heal (ferite gravi) presto: {_names(healers) or 'molto sustain nemico'}")

    tanks = [c for c in enemies if c["tank"] >= 7]
    if tanks and archetype not in ("tank", "enchanter"):
        pen = "penetrazione magica" if archetype in MAGIC else "penetrazione armatura"
        out.append(f"{pen.capitalize()} o danni a % di vita contro {_names(tanks)}")

    if theirs["cc"] >= 6.5:
        out.append("Tenacità o rimozione del CC: hanno molto controllo")

    if theirs["burst"] >= 7 and archetype in FRAGILE:
        out.append("Un oggetto difensivo anti-burst presto se ti focussano")

    if archetype == "enchanter":
        carry = max((c for c in allies if c.key != me.key), key=lambda c: c["dps"] + c["lategame"], default=None)
        if carry:
            out.append(f"Investi sugli oggetti che potenziano {carry.name}, il vostro carry principale")

    tempo = game_tempo(report)
    if tempo == "early":
        out.append("Ordine degli oggetti: punta al picco di forza presto, completa il primo oggetto in fretta")
    elif tempo == "late":
        out.append("Ordine degli oggetti: puoi permetterti oggetti da scaling, il tempo gioca per voi")
    return advice
