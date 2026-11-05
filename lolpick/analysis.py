"""Analisi delle composizioni e suggerimenti di pick, a partire dai voti dei campioni."""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean, pstdev

from .champions import Champion, ChampionDB
from .criteria import KEYS, LABELS, ROLE_LABELS, ROLES
from .draft import DraftState
from .lanes import LaneMatchup
from .laning import LaneStat, Laning
from .matchups import MatchupStat, Matchups
from .meta import VIABLE_PICK, Meta, MetaStat
from .roleplan import role_decisions

# Per questi criteri basta un campione forte (un tank, un engager...):
# il valore di squadra pesa di più il migliore che la media.
PRESENCE = {"engage", "disengage", "tank", "splitpush", "waveclear"}

# Criteri che, se bassi, sono un vero punto debole della composizione.
CORE = ["engage", "cc", "disengage", "tank", "dps", "burst", "waveclear", "sustain", "utility",
        "mobility", "earlygame", "lategame"]

STRONG, WEAK, EDGE = 6.5, 4.0, 1.2

ARCHETYPES = {
    "Dive": {"engage": 1.0, "mobility": 0.8, "burst": 0.8, "cc": 0.5},
    "Teamfight": {"engage": 0.8, "cc": 1.0, "dps": 0.8, "tank": 0.6},
    "Poke / Siege": {"poke": 1.0, "waveclear": 0.8, "disengage": 0.7, "range": 0.6},
    "Protect the carry": {"utility": 1.0, "disengage": 0.8, "dps": 1.0, "sustain": 0.4},
    "Pick": {"cc": 0.8, "burst": 1.0, "roaming": 0.8, "mobility": 0.5},
    "Split push": {"splitpush": 1.0, "waveclear": 0.7, "mobility": 0.6, "disengage": 0.4},
}

ARCHETYPE_PLANS = {
    "Dive": "Forzate fight entrando tutti insieme sul loro carry o su un bersaglio isolato; mai uno alla volta.",
    "Teamfight": "Raggruppatevi sugli obiettivi (drago, Herald, Baron) e cercate fight 5v5 frontali dove il vostro CC ad area decide.",
    "Poke / Siege": "Logorate da distanza prima di ogni obiettivo, assediate le torri e rifiutate i fight finché non sono bassi di vita.",
    "Protect the carry": "Giocate attorno al vostro carry: frontline e peel, fight in cui lui può colpire libero dal fondo.",
    "Pick": "Controllate la visione nella giungla e catturate bersagli isolati, poi convertite ogni kill in un obiettivo.",
    "Split push": "Pressione in side lane (1-3-1 o 1-4): costringete i nemici a rispondere mentre gli altri tengono il centro senza ingaggiare.",
}

BASE_WEIGHTS = {
    "engage": 1.0, "cc": 1.0, "tank": 1.0, "dps": 1.0, "waveclear": 0.8, "disengage": 0.7, "burst": 0.7,
    "utility": 0.6, "poke": 0.5, "lategame": 0.5, "earlygame": 0.5, "mobility": 0.4, "sustain": 0.4,
    "roaming": 0.3, "splitpush": 0.3, "range": 0.3,
}

# Se il nemico è forte nel criterio a sinistra, diventano più preziosi quelli a destra.
COUNTERS = {
    "engage": {"disengage": 1.0, "cc": 0.4},
    "poke": {"engage": 0.8, "sustain": 0.8},
    "burst": {"tank": 0.6, "utility": 0.6},
    "tank": {"dps": 1.0},
    "mobility": {"cc": 1.0},
    "dps": {"burst": 0.5, "engage": 0.5},
    "splitpush": {"waveclear": 0.6, "splitpush": 0.5},
    "sustain": {"burst": 0.5},
    "lategame": {"earlygame": 0.7},
    "earlygame": {"lategame": 0.4, "disengage": 0.4},
}

SATURATION = 7.0  # oltre questo voto un criterio di squadra rende poco in più
ARCHETYPE_BONUS = 1.5
DAMAGE_PENALTY = 8.0
MAIN_ROLE_BONUS = 0.4  # preferisce chi gioca quel ruolo come principale (no pick off-meta)
META_WEIGHT = 0.4  # peso della forza nel meta rispetto a quanto il pick serve al team (0 = solo team, 1 = solo meta)
META_FLOOR = -2.5  # z-score dato a chi non compare mai in quella lane nella tier list
MATCHUP_WEIGHT = 0.3  # peso dei matchup rispetto al resto del punteggio (0 = ignorati)
MATCHUP_SCALE = 2.0  # punti di win rate di vantaggio che valgono quanto una deviazione standard degli altri punteggi
LANE_WEIGHT = 0.35  # peso della fase di lane (GD15) rispetto a tutto il resto: è la voce singola più pesante
LANE_FLOOR = -1.5  # z-score dato a chi non ha dati di fase di lane in quel ruolo
MATCHUP_CAP = 3.0  # un singolo campione non può ribaltare tutto il resto per un matchup estremo


def team_profile(champions: list[Champion]) -> dict[str, float]:
    if not champions:
        return {k: 0.0 for k in KEYS}
    profile = {}
    for k in KEYS:
        values = [c[k] for c in champions]
        mean = sum(values) / len(values)
        profile[k] = 0.6 * max(values) + 0.4 * mean if k in PRESENCE else mean
    return profile


def physical_share(champions: list[Champion]) -> float | None:
    """Quota di danni fisici sul totale (0-1)."""
    phys = sum(c["physical"] for c in champions)
    magic = sum(c["magic"] for c in champions)
    return phys / (phys + magic) if champions else None


def archetype_fits(profile: dict[str, float]) -> list[tuple[str, float]]:
    fits = [(name, sum(profile[k] * w for k, w in ws.items()) / sum(ws.values())) for name, ws in ARCHETYPES.items()]
    return sorted(fits, key=lambda x: x[1], reverse=True)


def damage_penalty(champions: list[Champion]) -> float:
    share = physical_share(champions)
    if share is None:
        return 0.0
    deviation = max(0.0, abs(share - 0.5) - 0.2)  # fino a 70/30 nessuna penalità
    return DAMAGE_PENALTY * deviation * len(champions) / 5


def need_weights(enemy_profile: dict[str, float]) -> tuple[dict[str, float], dict[str, str]]:
    """Pesi dei criteri per la nostra squadra, aumentati in risposta ai punti forti nemici.

    Restituisce anche, per ogni criterio potenziato, il criterio nemico che lo rende utile.
    """
    weights = dict(BASE_WEIGHTS)
    because: dict[str, tuple[str, float]] = {}
    for enemy_k, answers in COUNTERS.items():
        pressure = max(0.0, enemy_profile[enemy_k] - 5) / 5
        for k, w in answers.items():
            boost = w * pressure
            weights[k] += boost
            if boost > 0.35 and boost > because.get(k, ("", 0))[1]:
                because[k] = (enemy_k, boost)
    return weights, {k: v[0] for k, v in because.items()}


def _saturate(x: float) -> float:
    return min(x, SATURATION) / SATURATION


def team_quality(champions: list[Champion], weights: dict[str, float]) -> float:
    profile = team_profile(champions)
    score = sum(w * _saturate(profile[k]) for k, w in weights.items())
    score += ARCHETYPE_BONUS * archetype_fits(profile)[0][1] / 10
    return score - damage_penalty(champions)


@dataclass
class Suggestion:
    champion: Champion
    score: float
    reasons: list[str]
    meta: MetaStat | None = None
    matchups: list[MatchupStat] = field(default_factory=list)  # contro i nemici lockati, dal più influente
    lane: LaneStat | None = None


def _reasons(team: list[Champion], candidate: Champion, weights: dict[str, float], because: dict[str, str]) -> list[str]:
    before, after = team_profile(team), team_profile(team + [candidate])
    gains = sorted(
        ((weights[k] * (_saturate(after[k]) - _saturate(before[k])), k) for k in weights),
        reverse=True,
    )
    reasons = []
    for gain, k in gains[:2]:
        if gain <= 0.05:
            break
        text = LABELS[k]
        if k in because:
            text += f" (contro il loro {LABELS[because[k]].lower()})"
        reasons.append(text)
    if team and damage_penalty(team + [candidate]) < damage_penalty(team) - 0.2:
        reasons.append("bilancia i danni " + ("magici" if candidate["magic"] > candidate["physical"] else "fisici"))
    return reasons


def _zscores(values: list[float]) -> list[float]:
    mu, sigma = mean(values), pstdev(values) or 1.0
    return [(v - mu) / sigma for v in values]


def suggest_picks(
    team: list[Champion],
    enemies: list[Champion],
    roles: list[str],
    unavailable: set[int],
    db: ChampionDB,
    limit: int = 5,
    meta: Meta | None = None,
    meta_weight: float = META_WEIGHT,
    matchups: Matchups | None = None,
    matchup_weight: float = MATCHUP_WEIGHT,
    opponents: list[tuple[Champion, str, bool]] | None = None,
    laning: Laning | None = None,
    lane_weight: float = LANE_WEIGHT,
) -> list[Suggestion]:
    """Migliori campioni per uno slot, in ordine di consiglio, dati i pick già lockati da entrambe le squadre.

    Il punteggio è una media pesata tra quanto il pick copre i bisogni del team (contro questi nemici),
    quanto è forte nel meta della lane, che vantaggio ha contro i nemici già scelti (`opponents`:
    campione, ruolo, ruolo certo) e quanto oro è atteso avere al minuto 15. Il vantaggio non è uno z-score:
    con pochi nemici noti deve contare poco.
    """
    weights, because = need_weights(team_profile(enemies)) if enemies else (dict(BASE_WEIGHTS), {})
    def plays(c: Champion) -> bool:  # ruolo da CSV, o giocato abbastanza in quella lane secondo la tier list
        return any(r in c.roles for r in roles) or bool(meta and (st := meta.best(c, roles)) and st.pickrate >= VIABLE_PICK)

    candidates = [c for c in db.all if c.key not in unavailable and plays(c)]
    if not candidates:
        return []
    fits = _zscores([team_quality(team + [c], weights) + (MAIN_ROLE_BONUS if c.roles[0] in roles else 0) for c in candidates])
    stats = [meta.best(c, roles) for c in candidates] if meta else [None] * len(candidates)
    base = [(1 - meta_weight) * f + meta_weight * (st.z if st else META_FLOOR) for f, st in zip(fits, stats)] if meta else fits
    lane_roles = [st.role if st else next((r for r in roles if r in c.roles), roles[0]) for c, st in zip(candidates, stats)]
    versus = [matchups.versus(c, r, opponents) if matchups and opponents else [] for c, r in zip(candidates, lane_roles)]
    lanes = [laning.versus(c, r, opponents or []) for c, r in zip(candidates, lane_roles)] if laning else [None] * len(candidates)
    known = _zscores([l.estimate for l in lanes if l]) if any(lanes) else []
    lane_z = iter(known)
    lane_scores = [next(lane_z) if l else LANE_FLOOR for l in lanes]
    def score(b: float, vs: list[MatchupStat], lane: float) -> float:
        edge = sum(m.edge for m in vs) / MATCHUP_SCALE
        total = (1 - matchup_weight) * b + matchup_weight * max(-MATCHUP_CAP, min(MATCHUP_CAP, edge))
        return (1 - lane_weight) * total + lane_weight * lane if laning else total

    ranked = sorted(((score(b, vs, lz), c, st, vs, l) for b, c, st, vs, l, lz in zip(base, candidates, stats, versus, lanes, lane_scores)),
                    key=lambda x: x[0], reverse=True)[:limit]
    return [Suggestion(c, s, _reasons(team, c, weights, because), st, vs, l) for s, c, st, vs, l in ranked]


@dataclass
class Report:
    ours: dict[str, float]
    theirs: dict[str, float]
    our_physical: float | None
    their_physical: float | None
    our_archetypes: list[tuple[str, float]]
    their_archetypes: list[tuple[str, float]]
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    their_strengths: list[str] = field(default_factory=list)
    their_weaknesses: list[str] = field(default_factory=list)
    advantages: list[tuple[str, float]] = field(default_factory=list)
    disadvantages: list[tuple[str, float]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _strong(profile: dict[str, float]) -> list[str]:
    keys = sorted((k for k in KEYS if k not in ("physical", "magic") and profile[k] >= STRONG),
                  key=lambda k: profile[k], reverse=True)
    return keys[:6]


def _weak(profile: dict[str, float]) -> list[str]:
    return sorted((k for k in CORE if profile[k] <= WEAK), key=lambda k: profile[k])


def _warnings(team: list[Champion], ours: dict[str, float], theirs: dict[str, float], enemies: list[Champion]) -> list[str]:
    if not team:
        return []
    out = []
    share = physical_share(team)
    if len(team) >= 3 and share is not None:
        if share > 0.75:
            out.append(f"Danni quasi solo fisici ({share:.0%}): l'armatura nemica vi neutralizza")
        elif share < 0.25:
            out.append(f"Danni quasi solo magici ({1 - share:.0%}): la resistenza magica nemica vi neutralizza")
    if len(team) >= 4:
        if ours["tank"] < 5:
            out.append("Manca una frontline")
        if ours["engage"] < 5 and ours["disengage"] < 5:
            out.append("Né engage né disengage: difficile scegliere quando combattere")
        if ours["waveclear"] < 5:
            out.append("Poco waveclear: soffrirete gli assedi")
    if enemies:
        if theirs["engage"] >= 7 and ours["disengage"] < 5:
            out.append("Il loro hard engage è pericoloso e avete poco disengage")
        if theirs["poke"] >= 6.5 and ours["engage"] < 6 and ours["sustain"] < 5:
            out.append("Il loro poke vi logorerà: serve engage o sustain")
        if theirs["mobility"] >= 7 and ours["cc"] < 5:
            out.append("Nemici molto mobili e poco CC per fermarli")
    return out


def analyze(team: list[Champion], enemies: list[Champion]) -> Report:
    ours, theirs = team_profile(team), team_profile(enemies)
    report = Report(
        ours=ours,
        theirs=theirs,
        our_physical=physical_share(team),
        their_physical=physical_share(enemies),
        our_archetypes=archetype_fits(ours),
        their_archetypes=archetype_fits(theirs),
    )
    if team:
        report.strengths, report.weaknesses = _strong(ours), _weak(ours)
    if enemies:
        report.their_strengths, report.their_weaknesses = _strong(theirs), _weak(theirs)
    if team and enemies:
        diffs = [(k, ours[k] - theirs[k]) for k in KEYS if k not in ("physical", "magic")]
        report.advantages = sorted([d for d in diffs if d[1] >= EDGE], key=lambda d: -d[1])
        report.disadvantages = sorted([d for d in diffs if d[1] <= -EDGE], key=lambda d: d[1])
    report.warnings = _warnings(team, ours, theirs, enemies)
    return report


TEMPO_TEXT = {
    "early": "Siete più forti presto e loro scalano meglio: forzate obiettivi early e chiudete prima del 30'.",
    "late": "Scalate meglio voi: giocate safe, farmate ed evitate fight inutili finché non avete 2-3 item.",
    "ahead": "Siete avanti sia early che late: giocate aggressivi ma senza regalare kill.",
    "behind": "Sulla carta sono più forti in tutte le fasi: puntate sulle debolezze specifiche e sugli errori di posizionamento.",
    "even": "Nessuna fase nettamente favorevole: decidono i matchup di lane e gli obiettivi.",
}


def game_tempo(report: Report) -> str:
    """In che fase la nostra composizione è più forte rispetto alla loro."""
    early = report.ours["earlygame"] - report.theirs["earlygame"]
    late = report.ours["lategame"] - report.theirs["lategame"]
    if early >= 0.5 and late <= 0:
        return "early"
    if late >= 0.5 and early <= 0:
        return "late"
    if early >= 0.5 and late >= 0.5:
        return "ahead"
    if early <= -0.5 and late <= -0.5:
        return "behind"
    return "even"


def heuristic_plan(report: Report, state: DraftState, lanes: list[LaneMatchup] | None = None) -> list[str]:
    """Piano di gioco a regole, usato subito (e come ripiego se Ollama non risponde)."""
    me_slot = state.me
    role, me = (me_slot.role, me_slot.champion) if me_slot else ("", None)
    archetype = report.our_archetypes[0][0]
    tempo = game_tempo(report)
    plan = [f"Identità: {archetype}. {ARCHETYPE_PLANS[archetype]}", TEMPO_TEXT[tempo]]

    their_archetype = report.their_archetypes[0][0]
    plan.append(f"Loro sono una composizione {their_archetype}: " + {
        "Dive": "tenete le distanze, raggruppatevi intorno al carry e punite chi entra per primo.",
        "Teamfight": "evitate i fight 5v5 in spazi stretti, cercate di dividerli.",
        "Poke / Siege": "non restate a farvi pokare: ingaggiate o ritiratevi, mai a metà.",
        "Protect the carry": "il loro carry è la condizione di vittoria: focalizzatelo o fatelo giocare lontano.",
        "Pick": "muovetevi in gruppo e con visione, niente facecheck.",
        "Split push": "rispondete al loro split con chi ha waveclear e forzate obiettivi 5v4.",
    }[their_archetype])

    if lanes:  # ruoli di tutti noti: decisioni concrete per il ruolo
        plan += [f"{d.topic}: {d.text}" for d in role_decisions(state, report, lanes, tempo)]
    elif me:
        plan += _role_tips(role, me, report)
    return plan


def _role_tips(role: str, me: Champion, report: Report) -> list[str]:
    ours, theirs = report.ours, report.theirs
    name = me.name
    tips = []
    if role == "top":
        if me["splitpush"] >= 7:
            tips.append(f"Con {name} sei la minaccia in side lane: tieni pressione e usa il TP per raggiungere i fight.")
        elif me["tank"] >= 7:
            tips.append(f"Con {name} sei la frontline: arriva per primo ai fight e assorbi il loro engage.")
        else:
            tips.append(f"Con {name} gioca la lane per vincere il 1v1 e trasforma il vantaggio in torri e Herald.")
    elif role == "jungle":
        if ours["earlygame"] > theirs["earlygame"] + 0.5:
            tips.append("Il vostro early è migliore: gank presto, invade e prendi controllo dei primi draghi.")
        else:
            tips.append("Farma bene, traccia il jungler nemico e contesta solo quando le tue lane hanno priorità.")
        if me["engage"] >= 7:
            tips.append(f"Con {name} sei tu ad aprire i fight: cerca angoli laterali sul loro carry.")
    elif role == "middle":
        if me["roaming"] >= 7:
            tips.append(f"{name} roama bene: spingi l'onda e muoviti verso le side lane o con il jungler.")
        if me["waveclear"] >= 7:
            tips.append("Tieni la priorità in mid per permettere a jungle e support di muoversi per primi.")
        if me["burst"] >= 8:
            tips.append("Nei fight cerca il bersaglio più fragile, non il primo che vedi.")
    elif role == "bottom":
        if me["dps"] >= 8:
            tips.append(f"{name} è la vostra fonte di danno sostenuto: stai dietro la frontline e colpisci ciò che hai a portata.")
        if theirs["engage"] >= 7 or theirs["burst"] >= 7:
            tips.append("Il loro engage/burst cercherà te: posizionati con attenzione e tieni il flash per loro.")
    elif role == "utility":
        if me["engage"] >= 7:
            tips.append(f"Con {name} sei tu l'engage: cercalo in lane e prima degli obiettivi, con il team pronto a seguirti.")
        elif me["disengage"] >= 7 or me["utility"] >= 8:
            tips.append(f"Con {name} il tuo compito è il peel: tieni vivo il carry più che cercare kill.")
        tips.append("Visione: prepara la zona dell'obiettivo un minuto prima che spawni.")
    return tips


def open_slots_roles(allies: list, slot) -> list[str]:
    """Ruoli possibili per uno slot alleato: quello assegnato, o quelli non ancora coperti."""
    if slot.role:
        return [slot.role]
    taken = {s.role for s in allies if s.role and s is not slot}
    return [r for r in ROLES if r not in taken] or list(ROLES)


def role_label(role: str) -> str:
    return ROLE_LABELS.get(role, role or "?")
