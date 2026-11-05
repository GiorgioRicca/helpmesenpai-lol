"""Prompt per il gameplan: dati della draft + istruzioni diverse per ogni ruolo."""

from __future__ import annotations

from .analysis import TEMPO_TEXT, Report, game_tempo, role_label
from .champions import Champion
from .criteria import KEYS, LABELS
from .draft import DraftState, Slot
from .builds import BuildAdvice
from .lanes import LaneMatchup
from .meta import Meta
from .roleplan import role_decisions
from .summoners import spell_names

SYSTEM_PROMPT = """Sei un coach esperto di League of Legends (livello high elo). Dai un gameplan concreto per la partita.

Regole:
- Rispondi in italiano, conciso, con elenchi puntati.
- NON parlare delle singole abilità dei campioni (niente Q/W/E/R, nomi di abilità o meccaniche specifiche): rischi di sbagliarle. Ragiona solo su profili (voti 1-10 per criterio), ruoli, summoner spell, matchup di lane e fasi di gioco.
- I voti includono già l'effetto delle summoner indicate in tabella ("?" = non note).
- La bot lane è un 2v2: valuta ADC e support insieme come un'unica lane, ricordando che sono due giocatori con compiti diversi.
- Tieni insieme il quadro di squadra (in che fase siamo più forti) e quello delle singole lane: una composizione da late game può comunque sfruttare una lane facile da gankare, e una da early non deve forzare gank su lane senza setup.
- Per la build parla di archetipo, statistiche e tipi di oggetto (es. anti-heal, penetrazione, tenacità), MAI di nomi di oggetti specifici: cambiano a ogni patch.
- Il tier (se presente) dice quanto un campione è forte nel meta attuale in quella lane: usalo come indizio su chi può portare la partita, ma la composizione e i matchup contano di più.
- Usa i numeri forniti per giustificare le scelte, senza ripeterli tutti.
- NIENTE consigli generici validi in ogni partita ("non morire", "farma bene", "metti ward", "gioca safe", "comunica col team"). Ogni punto deve essere una decisione legata a QUESTA draft: quale lane, quale campione, in che momento, perché (con il dato che lo giustifica).
- La sezione "Decisioni calcolate" contiene conclusioni già ricavate dai dati: partire da lì, spiegarle e trasformarle in un piano, senza contraddirle se non hai un motivo preciso preso dalle tabelle."""

ABBREV = {
    "physical": "Fis", "magic": "Mag", "utility": "Uti", "lategame": "Late", "earlygame": "Early",
    "engage": "Eng", "cc": "CC", "disengage": "Dis", "sustain": "Sus", "tank": "Tank", "roaming": "Roam",
    "mobility": "Mob", "burst": "Burst", "poke": "Poke", "dps": "DPS", "waveclear": "Wave", "splitpush": "Split", "range": "Rng",
}

# Istruzioni e sezioni specifiche per ruolo. {partner} è l'altro giocatore della bot lane.
ROLE_BRIEF = {
    "jungle": (
        "Sei il jungler: il gameplan dipende da dove passi il tempo.\n"
        "- Qual è la lane più conveniente da gankare e perché (setup dei nostri: CC, early, Ignite; vulnerabilità loro: escape, fragilità; priorità).\n"
        "- Pathing: da che lato della mappa iniziare per arrivare a quel gank al livello giusto, e dove finire il primo clear.\n"
        "- Dove probabilmente andrà il loro jungler e quale lane coprire.\n"
        "- Gank o farm in base al tempo della composizione e al matchup di jungle.",
        ["### Pathing iniziale e primo gank (0-6')", "### Lane da gankare e lane da proteggere",
         "### Gank o farm: come bilanciarli in questa partita", "### Obiettivi da contestare e con quali lane"],
    ),
    "top": (
        "Sei il top laner.\n"
        "- Matchup: vincere la lane, resistere o scalare, e se/quando chiamare il jungler.\n"
        "- Decisione chiave: partecipare ai teamfight, fare split push, o fare solo mirror del loro top. Se il loro top splitta e ti batte in 1v1, chi lo gestisce (tu, il mid che mirrora, tu + jungler)?\n"
        "- Se hai Teleport, quando usarlo (fight, obiettivi, flank).",
        ["### La tua lane e il jungler", "### Teamfight, split o mirror", "### Uso della summoner offensiva/Teleport"],
    ),
    "middle": (
        "Sei il mid laner.\n"
        "- Chi ha la priorità in mid e cosa ci fai (muoverti per primo o tenere l'onda).\n"
        "- Verso quale side lane conviene roamare, perché (setup, vulnerabilità, priorità) e in che momento.\n"
        "- Che ruolo hai nei teamfight.",
        ["### Priorità in mid", "### Dove e quando roamare", "### Il tuo ruolo nei teamfight"],
    ),
    "bottom": (
        "Sei l'ADC: la tua lane è un 2v2 con il tuo support {partner}.\n"
        "- Come impostare la lane come duo: priorità, range rispetto al loro ADC, quando all-in e quando farm.\n"
        "- Come fightare: front to back, aspettare il loro dive, cercare il pick, oppure sopravvivere all'engage e rispondere. Scegline uno e spiega perché.\n"
        "- Chi ti minaccia nei fight e chi ti protegge.",
        ["### Impostazione della bot lane (tu + {partner})", "### Come fightare", "### Minacce e protezione"],
    ),
    "utility": (
        "Sei il support: la tua lane è un 2v2 con il tuo ADC {partner}.\n"
        "- Stare in lane o roamare, e quando (early o mid/late) e verso dove.\n"
        "- Chi peelare e da quali minacce, oppure se sei tu ad aprire i fight.\n"
        "- Come vi muovete come duo verso gli obiettivi.",
        ["### Lane o roaming, e quando", "### Chi peelare (o quando ingaggiare)", "### Bot lane 2v2 (tu + {partner}) e obiettivi"],
    ),
}


def _champ_row(slot: Slot, meta: Meta | None) -> str:
    c: Champion = slot.champion
    spells = spell_names(c) or "?"
    tag = " (io)" if slot.is_me else ""
    stat = meta.for_role(c, slot.role) if meta else None
    tier = f" {stat.tier} |" if meta and stat else " ? |" if meta else ""
    return f"| {role_label(slot.role)}{tag} | {c.name} | {spells} |{tier} " + " | ".join(f"{c[k]:.0f}" for k in KEYS) + " |"


def _champ_table(slots: list[Slot], meta: Meta | None) -> list[str]:
    cols = ["Ruolo", "Campione", "Summoner", *(["Tier"] if meta else []), *(ABBREV[k] for k in KEYS)]
    return ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols), *(_champ_row(s, meta) for s in slots)]


def _lane_lines(lanes: list[LaneMatchup]) -> list[str]:
    lines = ["| Lane | Noi | Loro | Pressione early noi/loro | Esito | Priorità noi/loro | Chi ha priorità | GD15 atteso nostro | Facilità gank del nostro jungler | Esposizione ai loro gank |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for l in lanes:
        names = lambda cs: " + ".join(c.name for c in cs)
        gank = lambda v: "—" if v is None else f"{v:.1f}"
        gold = "—" if l.gd15 is None else f"{l.gd15:+.0f}"
        prio = lambda l: "— | — | —" if l.lane == "jungle" else f"{l.our_prio:.1f} / {l.their_prio:.1f} | {l.prio} | {gold}"
        lines.append(f"| {l.label} | {names(l.ours)} | {names(l.theirs)} | {l.our_pressure:.1f} / {l.their_pressure:.1f} "
                     f"| {l.verdict} | {prio(l)} | {gank(l.gank_for_us)} | {gank(l.gank_for_them)} |")
    return lines


def _build_lines(build: BuildAdvice | None) -> list[str]:
    if not build:
        return []
    reason = f" ({build.reason})" if build.reason else ""
    return ["", "## Build calcolata per me",
            f"- Archetipo: {build.label}{reason}: {build.stats}",
            *(f"- {a}" for a in build.adaptations)]


def build_prompt(state: DraftState, report: Report, lanes: list[LaneMatchup], build: BuildAdvice | None = None,
                 meta: Meta | None = None) -> str:
    me = state.me
    role = me.role if me else ""
    my_champ = me.champion.name if me and me.champion else "?"
    partner_role = {"bottom": "utility", "utility": "bottom"}.get(role)
    partner = next((s.champion.name for s in state.allies if partner_role and s.role == partner_role and s.champion), "?")
    brief, sections = ROLE_BRIEF.get(role, ("Analizza la partita per la squadra.", []))
    labels = lambda keys: ", ".join(LABELS[k] for k in keys) or "nessuno in particolare"
    legend = ", ".join(f"{ABBREV[k]}={LABELS[k]}" for k in KEYS)
    inferred = any(s.role_inferred for s in state.enemies)

    return "\n".join([
        "## Campioni (voti 1-10)",
        f"Legenda: {legend}."
        + (f" Tier = forza nel meta della patch {meta.patch} in Diamond+ (S+ … D), da pick rate e win rate nella lane." if meta else ""),
        "",
        "La mia squadra:", *_champ_table(state.allies, meta),
        "",
        "Squadra avversaria" + (" (ruoli dedotti dai campioni)" if inferred else "") + ":", *_champ_table(state.enemies, meta),
        "",
        "## Lane",
        "Pressione early = forza nella fase di lane. Priorità = chi spinge e si muove per primo (early, waveclear, CC, range e GD15). "
        "GD15 = distacco d'oro atteso al minuto 15 dai dati reali Diamond+. "
        "Facilità gank = setup degli alleati in lane + vulnerabilità degli avversari (0-10).",
        *_lane_lines(lanes),
        "",
        "## Squadre a confronto",
        "| Criterio | Noi | Loro |", "|---|---|---|",
        *(f"| {LABELS[k]} | {report.ours[k]:.1f} | {report.theirs[k]:.1f} |" for k in KEYS),
        f"- Danni: noi {report.our_physical:.0%} fisici, loro {report.their_physical:.0%} fisici",
        f"- Archetipo: noi {report.our_archetypes[0][0]}, loro {report.their_archetypes[0][0]}",
        f"- Tempo della partita: {TEMPO_TEXT[game_tempo(report)]}",
        f"- Nostri punti forti: {labels(report.strengths)}; deboli: {labels(report.weaknesses)}",
        f"- Loro punti forti: {labels(report.their_strengths)}; deboli: {labels(report.their_weaknesses)}",
        *(f"- Attenzione: {w}" for w in report.warnings),
        "",
        *_build_lines(build),
        "",
        f"## Il mio ruolo: {role_label(role)} con {my_champ}",
        "### Decisioni calcolate (dai dati sopra)",
        *(f"- {d.topic}: {d.text}" for d in role_decisions(state, report, lanes, game_tempo(report))),
        "",
        brief.format(partner=partner),
        "",
        "Scrivi il gameplan con queste sezioni:",
        "### Identità delle due composizioni e win condition",
        "### Punti forti e deboli (nostri e loro)",
        *(s.format(partner=partner) for s in sections),
        f"### Build ({my_champ}): archetipo, priorità delle statistiche e adattamenti contro questi avversari",
        "### Piano per fasi: early (0-14'), mid (14-25'), late",
        "### Errori da evitare in questa draft (specifici di questi campioni e matchup)",
    ])
