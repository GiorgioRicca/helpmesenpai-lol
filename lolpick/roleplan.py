"""Decisioni concrete per il proprio ruolo, calcolate dai voti: dove sta il vantaggio e come sfruttarlo.

Ogni decisione nomina lane, campioni e momento della partita, così l'LLM parte da conclusioni
già motivate invece di dare consigli generici.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .champions import Champion
from .draft import DraftState
from .lanes import LaneMatchup

if TYPE_CHECKING:
    from .analysis import Report

IGNITE, TELEPORT = 14, 12
SIDE = {"top": "top", "bot": "bot"}  # lato della mappa per lane
GANK_MIN = 5.5  # sotto questa facilità un gank non vale il viaggio


@dataclass
class Decision:
    topic: str
    text: str


def _names(champions: list[Champion]) -> str:
    return " + ".join(c.name for c in champions)


def _ally(state: DraftState, role: str, team: str = "allies") -> Champion | None:
    return next((s.champion for s in getattr(state, team) if s.role == role and s.champion), None)


def _lanes(lanes: list[LaneMatchup]) -> dict[str, LaneMatchup]:
    return {l.lane: l for l in lanes}


def _gank_value(l: LaneMatchup) -> float:
    """Facilità del gank, più un bonus se gli avversari hanno priorità: spingono e si sbilanciano verso di noi."""
    return l.gank_for_us + 0.25 * (l.their_prio - l.our_prio)


def _gank_reasons(l: LaneMatchup) -> str:
    reasons = []
    ignite = [c.name for c in l.ours if IGNITE in c.spells]
    if ignite:
        reasons.append(f"Ignite su {', '.join(ignite)}")
    if max(c["cc"] for c in l.ours) >= 6:
        reasons.append("CC per il follow-up")
    if max(c["earlygame"] for c in l.ours) >= 7:
        reasons.append("alleato forte in early")
    if min(c["mobility"] for c in l.theirs) <= 4:
        reasons.append(f"{min(l.theirs, key=lambda c: c['mobility']).name} ha pochi escape")
    if min(c["tank"] for c in l.theirs) <= 3:
        reasons.append(f"{min(l.theirs, key=lambda c: c['tank']).name} è fragile")
    if l.prio == "loro":
        reasons.append("loro hanno priorità e spingono verso la vostra torre")
    return ", ".join(reasons) or "nessun fattore netto"


def _duel(c: Champion) -> float:
    """Forza nell'1v1 in side lane."""
    return 0.4 * c["dps"] + 0.3 * c["sustain"] + 0.3 * c["splitpush"]


def priority_lines(lanes: list[LaneMatchup]) -> list[Decision]:
    out = []
    for l in lanes:
        if l.lane == "jungle":
            continue
        who = {"noi": "la vostra", "loro": "la loro", "contesa": "contesa"}[l.prio]
        gold = f", distacco d'oro atteso al 15° {l.gd15:+.0f}" if l.gd15 is not None else ""
        out.append(Decision(f"Priorità {l.label}", f"{who} ({l.our_prio:.1f} vs {l.their_prio:.1f}{gold})"))
    return out


def _jungle(me: Champion, state: DraftState, report: Report, lanes: dict[str, LaneMatchup], tempo: str) -> list[Decision]:
    out = []
    sides = [l for k, l in lanes.items() if k in ("top", "middle", "bot")]
    if not sides:
        return out
    target = max(sides, key=_gank_value)
    if target.gank_for_us >= GANK_MIN:
        out.append(Decision("Lane da gankare", f"{target.label}: {_names(target.ours)} vs {_names(target.theirs)}, "
                            f"facilità {target.gank_for_us:.1f}/10 ({_gank_reasons(target)})."))
    else:
        out.append(Decision("Lane da gankare", "Nessuna lane ha un gank facile (tutte sotto 5.5/10): "
                            "priorità al farm e agli obiettivi, gank solo su errori di posizionamento."))

    # Il primo clear finisce sul lato opposto a quello da cui parti.
    prio_side = max((l for l in sides if l.lane in SIDE), key=lambda l: l.our_prio - l.their_prio, default=None)
    if target.gank_for_us >= GANK_MIN and target.lane in SIDE:
        start = "bot" if target.lane == "top" else "top"
        out.append(Decision("Pathing", f"Inizia dal lato {start} della mappa e chiudi il clear verso {target.lane}: "
                            f"arrivi al gank {target.label} al livello 3-4 (circa 3:00-3:30)."))
    elif prio_side:
        start = "bot" if prio_side.lane == "top" else "top"
        then = (f", poi passa dal mid per il gank {_names(target.ours)} vs {_names(target.theirs)}"
                if target.lane == "middle" and target.gank_for_us >= GANK_MIN else "")
        out.append(Decision("Pathing", f"Inizia dal lato {start} e chiudi il primo clear sul lato {prio_side.lane}, dove "
                            f"{prio_side.label} ha priorità {prio_side.prio}: lì contesti lo scuttle coperto dalla lane{then}."))

    jungle = lanes.get("jungle")
    if jungle:
        if jungle.verdict == "favorevole" and prio_side and prio_side.prio == "noi":
            out.append(Decision("Invade", f"Sei più forte del loro jungler in early: invadi il lato {prio_side.lane}, "
                                f"dove {_names(prio_side.ours)} possono seguirti per primi."))
        elif jungle.verdict == "sfavorevole":
            out.append(Decision("Jungler nemico", f"{_names(jungle.theirs)} è più forte di te in early: niente invade, "
                                "niente 1v1 sullo scuttle senza lane con priorità."))

    their_target = max(sides, key=lambda l: l.gank_for_them)
    if their_target.gank_for_them >= GANK_MIN:
        text = f"Il loro jungler ha il gank più facile su {their_target.label} ({their_target.gank_for_them:.1f}/10)"
        if their_target.lane in SIDE:
            opposite = "bot" if their_target.lane == "top" else "top"
            text += f": probabile che inizi dal lato {opposite} per arrivarci al livello 3-4."
        else:
            text += ": aspettalo in mid dopo il suo primo clear, quando il tuo mid non ha visione del fiume."
        if their_target is target:
            text += " È la stessa lane che vuoi gankare tu: arrivaci per primo o resta vicino per il controgank."
        else:
            text += f" Se lì non puoi coprire, gioca sul lato {target.label} e scambia obiettivi."
        out.append(Decision("Lane da proteggere", text))

    farm = {"early": "Composizione da early: più gank e invade, il tempo gioca contro di voi.",
            "late": "Composizione da late: gank solo se facili (sopra), altrimenti farm e copertura delle lane che scalano.",
            "ahead": "Siete più forti sia presto sia tardi: gioca aggressivo dove hai priorità.",
            "behind": "Siete più deboli in ogni fase: farm, visione e controgank, punta sui loro errori.",
            "even": "Tempo equilibrato: decidono i gank riusciti, scegli quelli ad alta facilità."}[tempo]
    out.append(Decision("Gank o farm", farm))
    return out


def _middle(me: Champion, state: DraftState, report: Report, lanes: dict[str, LaneMatchup]) -> list[Decision]:
    out = []
    mid = lanes.get("middle")
    sides = [l for k, l in lanes.items() if k in SIDE]
    if mid:
        out.append(Decision("Priorità in mid", {
            "noi": "Ce l'hai tu: spingi l'onda e muoviti per primo (jungler, scuttle, roam).",
            "loro": "Ce l'ha il loro mid: non lasciare l'onda per roamare, gioca il farm e segui i suoi movimenti con un ping.",
            "contesa": "Contesa: roama solo dopo aver spinto un'onda intera, altrimenti perdi farm e piatti.",
        }[mid.prio]))
    if sides:
        target = max(sides, key=_gank_value)
        if me["roaming"] >= 6 or me["mobility"] >= 7:
            when = "dopo aver spinto l'onda" if not mid or mid.prio != "loro" else "solo quando l'onda è spinta (raro: non hai priorità)"
            out.append(Decision("Dove roamare", f"Verso {target.label}: {_names(target.ours)} vs {_names(target.theirs)}, "
                                f"facilità {target.gank_for_us:.1f}/10 ({_gank_reasons(target)}). Vai {when}."))
        else:
            out.append(Decision("Dove roamare", f"{me.name} non è un campione da roam: muoviti solo verso {target.label} "
                                "insieme al jungler, quando l'onda è spinta, o per gli obiettivi."))
        other = next((l for l in sides if l is not target), None)
        if other and other.gank_for_them >= GANK_MIN:
            out.append(Decision("Lato da coprire", f"{other.label} è esposta ai loro gank ({other.gank_for_them:.1f}/10): "
                                "se il loro jungler si mostra lì, muoviti tu per il controgank."))
    their_mid = _ally(state, "middle", "enemies")
    if their_mid and their_mid["roaming"] >= 7:
        out.append(Decision("Loro mid", f"{their_mid.name} roama molto: quando sparisce pinga subito e "
                            "scegli se seguirlo o prendere piatti con l'onda spinta."))
    return out


def _top(me: Champion, state: DraftState, report: Report, lanes: dict[str, LaneMatchup]) -> list[Decision]:
    out = []
    top = lanes.get("top")
    enemy = _ally(state, "top", "enemies")
    teamfight = (me["engage"] + me["tank"] + me["cc"]) / 3
    if me["splitpush"] >= 7 and me["splitpush"] >= teamfight + 1:
        mode = (f"Split push: dal mid game resta in side lane (1-3-1 / 1-4). {me.name} vale più lì che nei 5v5: "
                "il team non deve forzare fight senza di te e tu rientri solo per obiettivi grossi.")
    elif teamfight >= 6:
        mode = (f"Teamfight: {me.name} serve al gruppo come frontline/engage ({teamfight:.1f}/10). "
                "Dopo la fase di lane spingi la side e raggiungi il team per drago e Baron.")
    else:
        mode = "Mirror: tieni la side lane opposta al loro splitpusher e raggiungi il team solo quando loro sono in 4."
    out.append(Decision("Fight o side lane", mode))

    if enemy and enemy["splitpush"] >= 7:
        if _duel(me) >= _duel(enemy) - 0.5:
            out.append(Decision("Il loro split", f"{enemy.name} farà split push, ma nel 1v1 te la giochi "
                                f"({_duel(me):.1f} vs {_duel(enemy):.1f}): mirrorare spetta a te."))
        else:
            mid = _ally(state, "middle")
            helper = (f"chiedi a {mid.name} di mirrorare" if mid and mid["waveclear"] >= 7
                      else "rispondi in 2 con il jungler")
            out.append(Decision("Il loro split", f"{enemy.name} ti batte in 1v1 in side lane ({_duel(enemy):.1f} vs {_duel(me):.1f}): "
                                f"non fermarlo da solo, {helper} mentre il team forza obiettivi in 4v4."))
    if top:
        if top.verdict == "sfavorevole":
            ask = (f"chiama il jungler: la tua lane ha facilità gank {top.gank_for_us:.1f}/10" if top.gank_for_us >= GANK_MIN
                   else "la lane non è facile da gankare: tieni l'onda vicino alla tua torre e scala")
            out.append(Decision("La tua lane", f"Sfavorevole in early: {ask}."))
        elif top.verdict == "favorevole":
            out.append(Decision("La tua lane", f"Favorevole in early con priorità {top.prio}: trade aggressivi, "
                                "piatti e Herald; il jungler può giocare sul tuo lato."))
        if top.gank_for_them >= GANK_MIN:
            out.append(Decision("Gank nemici", f"Sei esposto ai loro gank ({top.gank_for_them:.1f}/10): "
                                "senza visione del loro jungler non superare metà lane."))
    if TELEPORT in me.spells:
        out.append(Decision("Teleport", "Tienilo per i fight su drago/Herald e per i flank, non per tornare in lane "
                            "se non stai perdendo una torre."))
    return out


def _carry(allies: list[Champion], me: Champion) -> Champion | None:
    """L'alleato da proteggere: tanto danno e poca capacità di salvarsi da solo."""
    others = [c for c in allies if c.key != me.key]
    return max(others, key=lambda c: c["dps"] + c["lategame"] + 0.5 * c["burst"] - 0.5 * (c["mobility"] + c["tank"]),
               default=None)


def _threats(enemies: list[Champion], n: int = 2) -> list[Champion]:
    return sorted(enemies, key=lambda c: c["engage"] + c["mobility"] + c["burst"], reverse=True)[:n]


def _utility(me: Champion, state: DraftState, report: Report, lanes: dict[str, LaneMatchup], tempo: str) -> list[Decision]:
    out = []
    bot = lanes.get("bot")
    stay = bot is not None and (bot.verdict == "sfavorevole" or bot.prio == "loro")
    if me["roaming"] >= 6 and not stay:
        when = "già nell'early, quando l'onda è spinta e dopo il primo back" if tempo in ("early", "ahead") or me["earlygame"] >= 7 \
            else "dal mid game, dopo la prima torre bot"
        target = max((l for k, l in lanes.items() if k in ("middle", "top")), key=_gank_value, default=None)
        where = f" verso {target.label} ({_gank_reasons(target)})" if target else ""
        out.append(Decision("Lane o roaming", f"Roaming: {when}{where}. Il tuo ADC può reggere la lane da solo per un'onda."))
    elif stay:
        out.append(Decision("Lane o roaming", "Resta in lane: il 2v2 è "
                            + ("sfavorevole" if bot.verdict == "sfavorevole" else "con priorità loro")
                            + " e lasciare solo l'ADC significa regalare kill o piatti. Roama solo dal mid game con il jungler, per la visione."))
    else:
        out.append(Decision("Lane o roaming", f"{me.name} rende in lane più che in roaming: resta fino alla prima torre, "
                            "poi muoviti con il jungler per la visione degli obiettivi."))

    carry = _carry(state.ally_champions, me)
    threats = _threats(state.enemy_champions)
    if carry and threats:
        out.append(Decision("Chi peelare", f"{carry.name} (danno alto, pochi escape). Minacce principali: "
                            f"{', '.join(c.name for c in threats)}: tieni il tuo CC per loro, non per aprire."))
    if me["engage"] >= 7 and report.ours["engage"] - report.theirs["disengage"] >= 0:
        out.append(Decision("Engage o peel", f"Engage: sei tu ad aprire ({me.name}), quando il team è in range a seguirti."))
    elif me["engage"] >= 7:
        out.append(Decision("Engage o peel", "Hai engage, ma loro hanno disengage: ingaggia solo su bersagli isolati, nei 5v5 fai peel."))
    return out


def _bottom(me: Champion, state: DraftState, report: Report, lanes: dict[str, LaneMatchup]) -> list[Decision]:
    out = []
    bot = lanes.get("bot")
    their_adc = _ally(state, "bottom", "enemies")
    if their_adc:
        diff = me["range"] - their_adc["range"]
        if diff >= 2:
            out.append(Decision("Range", f"Outrangi {their_adc.name}: puniscilo ogni volta che va al last hit."))
        elif diff <= -2:
            out.append(Decision("Range", f"{their_adc.name} ti outranga: gioca per il farm e lascia aprire il support."))
    if bot:
        out.append(Decision("Bot lane 2v2", f"{bot.verdict.capitalize()} in early, priorità {bot.prio}: "
                            + {"favorevole": "cercate trade e all-in col support, poi drago e piatti.",
                               "equilibrata": "giocate per il farm e per il primo drago.",
                               "sfavorevole": "onda vicino alla vostra torre, niente trade senza il jungler."}[bot.verdict]))

    ours, theirs = report.ours, report.theirs
    our_arch, their_arch = report.our_archetypes[0][0], report.their_archetypes[0][0]
    peelers = [c.name for c in state.ally_champions if c.key != me.key and (c["disengage"] >= 7 or c["utility"] >= 8)]
    threats = _threats(state.enemy_champions)
    if their_arch == "Dive" or theirs["engage"] >= 6.5:
        style = (f"Sopravvivi all'engage e rispondi: entreranno su di te con {', '.join(c.name for c in threats)}. "
                 "Stai al limite del tuo range, tieni flash per loro e colpisci chi è entrato"
                 + (f"; ti proteggono {', '.join(peelers)}." if peelers else "; avete poco peel, posizionati ancora più indietro."))
    elif our_arch == "Poke / Siege":
        style = "Poke prima dei fight: logora da massimo range prima degli obiettivi, entra solo quando sono bassi."
    elif our_arch == "Pick":
        style = "Cerca il pick: muoviti col team nella visione, entra quando un bersaglio isolato viene catturato."
    elif ours["tank"] >= 6:
        style = "Front to back: la vostra frontline assorbe, tu colpisci il bersaglio più vicino senza superarla."
    else:
        style = "Kite: avete poca frontline, combatti in ritirata e non entrare per primo."
    out.append(Decision("Come fightare", style))
    if me["lategame"] >= 8 and me["earlygame"] <= 5:
        out.append(Decision("Tempo", f"{me.name} scala: la lane è da non perdere, non da vincere a ogni costo."))
    return out


def role_decisions(state: DraftState, report: Report, lanes: list[LaneMatchup], tempo: str) -> list[Decision]:
    me = state.me
    if not me or not me.champion:
        return []
    by_lane = _lanes(lanes)
    champ = me.champion
    return {
        "jungle": lambda: _jungle(champ, state, report, by_lane, tempo),
        "middle": lambda: _middle(champ, state, report, by_lane),
        "top": lambda: _top(champ, state, report, by_lane),
        "utility": lambda: _utility(champ, state, report, by_lane, tempo),
        "bottom": lambda: _bottom(champ, state, report, by_lane),
    }.get(me.role, list)()
