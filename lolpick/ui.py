"""Interfaccia nel terminale (rich): vista live durante la draft e report finale."""

from __future__ import annotations

from rich import box
from rich.columns import Columns
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .analysis import Report, Suggestion, heuristic_plan, role_label
from .builds import BuildAdvice, recommend_build
from .criteria import KEYS, LABELS
from .draft import DraftState, Slot
from .lanes import LaneMatchup
from .meta import Meta, MetaStat
from .summoners import spell_names

ALLY, ENEMY = "cyan", "red"
TIER_STYLE = {"S+": "bold magenta", "S": "bold green", "A": "green", "B": "", "C": "yellow", "D": "red"}


def _bar(value: float, color: str) -> Text:
    filled = int(round(value))
    return Text("█" * filled, style=color) + Text("·" * (10 - filled), style="grey35") + Text(f" {value:4.1f}")


def _games(n: int) -> str:
    return f"{n / 1000:.0f}k" if n >= 10_000 else f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _tier(stat: MetaStat | None) -> Text:
    """Tier, win rate e partite nel ruolo, es. "S  52.1% 98k"."""
    if not stat:
        return Text("—", style="grey35")
    return Text(f"{stat.tier:<2}", style=TIER_STYLE[stat.tier]) + Text(f" {stat.winrate:.1f}% {_games(stat.games)}", style="grey62")


def _team_table(title: str, slots: list[Slot], color: str, meta: Meta | None = None) -> Table:
    t = Table(title=title, title_style=f"bold {color}", box=box.SIMPLE, expand=True, show_header=False)
    t.add_column(width=13, no_wrap=True)
    t.add_column()
    t.add_column(style="grey62")
    t.add_column(no_wrap=True)
    t.add_column(justify="right")
    for s in slots:
        role = Text(role_label(s.role) + ("*" if s.role_inferred else ""), style="bold" if s.is_me else "")
        if s.is_me:
            role.append(" (tu)", style="yellow")
        if s.champion:
            champ, status = Text(s.champion.name, style=f"bold {color}"), Text("LOCK", style="green")
        elif s.hover:
            champ, status = Text(s.hover.name, style="italic grey62"), Text("hover", style="grey62")
        else:
            champ, status = Text("—", style="grey35"), Text("")
        shown = s.champion or s.hover
        tier = _tier(meta.for_role(shown, s.role)) if meta and shown else Text("")
        t.add_row(role, champ, spell_names(shown) if shown else "", tier, status)
    return t


def _comparison(report: Report) -> Table:
    t = Table(box=box.SIMPLE_HEAD, expand=True, padding=(0, 1))
    t.add_column("Criterio")
    t.add_column("Noi", style=ALLY)
    t.add_column("Loro", style=ENEMY)
    t.add_column("Δ", justify="right")
    for k in KEYS:
        diff = report.ours[k] - report.theirs[k]
        style = "green" if diff >= 1.2 else "red" if diff <= -1.2 else "grey50"
        t.add_row(LABELS[k], _bar(report.ours[k], ALLY), _bar(report.theirs[k], ENEMY), Text(f"{diff:+.1f}", style=style))
    return t


def _labels(keys: list[str]) -> str:
    return ", ".join(LABELS[k] for k in keys) or "—"


def _damage(share: float | None) -> str:
    return "—" if share is None else f"{share:.0%} AD / {1 - share:.0%} AP"


def _analysis_panel(report: Report, has_allies: bool, has_enemies: bool, build: BuildAdvice | None = None) -> Panel:
    lines = Text()
    if build:
        lines.append("Tua build  ", style="bold yellow")
        lines.append(build.label + (f" ({build.reason})" if build.reason else "") + "\n")
        for a in build.adaptations[:2]:
            lines.append(f"  · {a}\n", style="grey62")
    if has_allies:
        lines.append("Noi  ", style=f"bold {ALLY}")
        lines.append(f"{report.our_archetypes[0][0]} · {_damage(report.our_physical)}\n")
        lines.append("  + ", style="green")
        lines.append(_labels(report.strengths) + "\n")
        lines.append("  − ", style="red")
        lines.append(_labels(report.weaknesses) + "\n")
    if has_enemies:
        lines.append("Loro ", style=f"bold {ENEMY}")
        lines.append(f"{report.their_archetypes[0][0]} · {_damage(report.their_physical)}\n")
        lines.append("  + ", style="green")
        lines.append(_labels(report.their_strengths) + "\n")
        lines.append("  − ", style="red")
        lines.append(_labels(report.their_weaknesses) + "\n")
    for w in report.warnings:
        lines.append(f"⚠ {w}\n", style="yellow")
    return Panel(lines or Text("Nessun campione lockato", style="grey50"), title="Analisi", border_style="blue")


def _matchup(s: Suggestion) -> Text:
    """Il matchup che pesa di più sul consiglio, es. "vs Garen 56.1% 412"."""
    out = Text()
    if s.matchups:
        m = s.matchups[0]
        out.append(f" vs {m.enemy.name} {m.winrate:.1f}% {_games(m.games)}", style="green" if m.edge > 0 else "red")
    return out


def _lane(s: Suggestion, named: bool = False) -> Text:
    """Oro atteso al minuto 15; il nome dell'avversario di lane solo se non è già quello del matchup mostrato."""
    if not s.lane:
        return Text("")
    out = Text(f" GD15 {s.lane.estimate:+.0f}", style="green" if s.lane.estimate > 0 else "red")
    if named and s.lane.enemy and not (s.matchups and s.matchups[0].enemy is s.lane.enemy):
        out.append(f" {s.lane.enemy.name}", style="grey62")
    return out


def _pick_row(i: int, slot: Slot, s: Suggestion) -> Text:
    row = Text(f" {i:>2}. {s.champion.name}", style="bold")
    if s.meta:
        row.append(" ")
        row.append_text(_tier(s.meta))
        if s.meta.role != slot.role:  # ruolo non ancora noto: dati della lane migliore del campione
            row.append(f" {role_label(s.meta.role)}", style="grey50")
    row.append_text(_lane(s, named=True))
    row.append_text(_matchup(s))
    if s.reasons:
        row.append(" — " + ", ".join(s.reasons), style="grey62")
    return row


def _pick_cell(i: int, s: Suggestion) -> Text:
    return Text(f"{i}. {s.champion.name}", style="bold", no_wrap=True) + " " + _tier(s.meta) + _lane(s)


def _suggestions_panel(suggestions: list[tuple[Slot, list[Suggestion]]]) -> Panel:
    rows = []
    me_open = any(slot.is_me for slot, _ in suggestions)
    for slot, picks in suggestions:
        header = role_label(slot.role) + (" (tu)" if slot.is_me else "")
        rows.append(Text(header, style="bold yellow" if slot.is_me else "bold"))
        if slot.is_me or not me_open:
            rows += [_pick_row(i, slot, s) for i, s in enumerate(picks, 1)]
        else:  # compagni: più campioni per riga, per far stare tutto sullo schermo
            rows.append(Columns([_pick_cell(i, s) for i, s in enumerate(picks, 1)], padding=(0, 2)))
    body = Group(*rows) if rows else Text("Tutti gli alleati hanno lockato", style="grey50")
    return Panel(body, title="Pick consigliati", border_style="green")


def render_live(state: DraftState, report: Report, suggestions: list[tuple[Slot, list[Suggestion]]],
                meta: Meta | None = None) -> Group:
    header = Text("Champ select", style="bold")
    if state.phase:
        header.append(f"  ·  {state.phase}", style="grey62")
    if state.time_left is not None:
        header.append(f"  ·  {state.time_left:.0f}s", style="yellow")
    if state.bans:
        header.append(f"  ·  {len(state.bans)} ban", style="grey62")
    if meta:
        header.append(f"  ·  tier list {meta.patch} Diamond+", style="grey62")
    teams = _teams(state, meta)
    me = state.me
    my_champ = me and (me.champion or me.hover)
    build = recommend_build(my_champ, state.ally_champions, state.enemy_champions, report) if my_champ else None
    analysis = _analysis_panel(report, bool(state.ally_champions), bool(state.enemy_champions), build)
    # Live taglia ciò che esce dallo schermo: i consigli stanno in alto, accanto al confronto e non sotto.
    bottom = Table.grid(expand=True, padding=(0, 1))
    bottom.add_column(width=62)
    bottom.add_column(ratio=1)
    bottom.add_row(Group(_comparison(report), analysis), _suggestions_panel(suggestions))
    return Group(header, teams, bottom)


def _lanes_table(lanes: list[LaneMatchup]) -> Table:
    inferred = any(l.inferred for l in lanes)
    t = Table(title="Lane", title_style="bold", box=box.SIMPLE_HEAD, expand=True,
              caption="* ruoli nemici dedotti (Smite e campioni)" if inferred else None, caption_style="grey50")
    t.add_column("Lane")
    t.add_column("Noi", style=ALLY)
    t.add_column("Loro", style=ENEMY)
    t.add_column("Early noi/loro", justify="center")
    t.add_column("Esito")
    t.add_column("Priorità", justify="center")
    t.add_column("Gank nostro jungler", justify="center")
    t.add_column("Rischio loro gank", justify="center")
    gank = lambda v: "—" if v is None else Text(f"{v:.1f}", style="green" if v >= 5.5 else "")
    risk = lambda v: "—" if v is None else Text(f"{v:.1f}", style="red" if v >= 5.5 else "")
    for l in lanes:
        style = {"favorevole": "green", "sfavorevole": "red"}.get(l.verdict, "grey62")
        prio = Text("—") if l.lane == "jungle" else Text(f"{l.prio} {l.our_prio:.1f}/{l.their_prio:.1f}" + (f" GD15 {l.gd15:+.0f}" if l.gd15 is not None else ""),
                                                         style={"noi": "green", "loro": "red"}.get(l.prio, "grey62"))
        names = lambda cs: " + ".join(c.name for c in cs)
        t.add_row(l.label + ("*" if l.inferred else ""), names(l.ours), names(l.theirs),
                  f"{l.our_pressure:.1f}/{l.their_pressure:.1f}", Text(l.verdict, style=style), prio,
                  gank(l.gank_for_us), risk(l.gank_for_them))
    return t


def _build_panel(champ_name: str, build: BuildAdvice) -> Panel:
    body = Text(build.label, style="bold")
    if build.reason:
        body.append(f" — {build.reason}", style="grey62")
    body.append(f"\nStatistiche: {build.stats}\n")
    for a in build.adaptations:
        body.append(f"• {a}\n")
    return Panel(body, title=f"Build consigliata · {champ_name}", border_style="yellow")


def _teams(state: DraftState, meta: Meta | None) -> Columns:
    return Columns([_team_table("La tua squadra", state.allies, ALLY, meta),
                    _team_table("Avversari", state.enemies, ENEMY, meta)], expand=True, equal=True)


def render_final(state: DraftState, report: Report, lanes: list[LaneMatchup], build: BuildAdvice | None = None,
                 title: str = "Partita iniziata", meta: Meta | None = None) -> Group:
    me = state.me
    plan = heuristic_plan(report, state, lanes)
    plan_text = Text("\n".join(f"• {p}" for p in plan))
    return Group(
        Text(title, style="bold green"),
        _teams(state, meta),
        _comparison(report),
        _analysis_panel(report, True, True),
        *([_lanes_table(lanes)] if lanes else []),
        Panel(plan_text, title="Piano rapido (regole)", border_style="magenta"),
        *([_build_panel(me.champion.name, build)] if build and me and me.champion else []),
    )


def waiting(message: str) -> Panel:
    return Panel(Text(message, style="grey62"), title="lolpick", border_style="grey35")
