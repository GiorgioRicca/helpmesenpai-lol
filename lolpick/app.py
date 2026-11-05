"""Entrypoint: `uv run lolpick` (live), `--demo` (simulazione), `--analyze` (draft scritta a mano), `--update` (dati)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.live import Live

from .analysis import LANE_WEIGHT, MATCHUP_WEIGHT, META_WEIGHT, analyze, heuristic_plan, open_slots_roles, role_label, suggest_picks
from .champions import ChampionDB, load_champions
from .criteria import KEYS, LABELS, ROLE_ALIASES, ROLES
from .draft import DraftState, Slot, assign_missing_roles, infer_roles, parse_session
from .lcu import LCUNotRunning, LCUSource
from .livegame import GameSnapshot, LiveGameSource
from .builds import recommend_build
from .lanes import lane_matchups
from .laning import load_laning
from .matchups import load_matchups
from .meta import load_meta
from .llm import DEFAULT_HOST, DEFAULT_MODEL, OllamaError, stream_gameplan
from .prompts import build_prompt
from .summoners import find_spell, spell_names, with_spells
from .ui import render_final, render_live, waiting

FROZEN = getattr(sys, "frozen", False)  # eseguibile PyInstaller (lolpick.exe)
REPORTS_DIR = (Path(sys.executable).resolve().parent if FROZEN else Path.cwd()) / "reports"
UPDATE_CMD = "lolpick.exe --update" if FROZEN else "uv run lolpick-update"
PICKS_SHOWN = 10


def compute_suggestions(state: DraftState, db: ChampionDB, args) -> list:
    team, enemies = state.ally_champions, state.enemy_champions
    open_slots = sorted((s for s in state.allies if not s.locked), key=lambda s: not s.is_me)
    return [
        (slot, suggest_picks(team, enemies, open_slots_roles(state.allies, slot), state.unavailable(slot), db, limit=PICKS_SHOWN,
                             meta=args.meta, meta_weight=args.meta_weight, matchups=args.matchups,
                             matchup_weight=args.matchup_weight, opponents=state.opponents,
                             laning=args.laning, lane_weight=args.lane_weight))
        for slot in open_slots
    ]


def save_report(state: DraftState, report, lanes, build, plan: list[str], gameplan: str) -> Path:
    REPORTS_DIR.mkdir(exist_ok=True)
    me = state.me
    champ = me.champion.id if me and me.champion else "draft"
    path = REPORTS_DIR / f"{datetime.now():%Y-%m-%d_%H%M}_{champ}.md"
    team = lambda slots: "\n".join(
        f"- {role_label(s.role)}: {s.champion.name}" + (f" ({spell_names(s.champion)})" if s.champion.spells else "")
        + (" (io)" if s.is_me else "") for s in slots)
    names = lambda cs: " + ".join(c.name for c in cs)
    lane_rows = "\n".join(f"| {l.label} | {names(l.ours)} | {names(l.theirs)} | {l.verdict} | {'—' if l.lane == 'jungle' else l.prio} |" for l in lanes)
    rows = "\n".join(f"| {LABELS[k]} | {report.ours[k]:.1f} | {report.theirs[k]:.1f} |" for k in KEYS)
    path.write_text(
        f"# Draft del {datetime.now():%d/%m/%Y %H:%M}\n\n"
        f"## La mia squadra\n{team(state.allies)}\n\n## Avversari\n{team(state.enemies)}\n\n"
        f"## Profilo\n| Criterio | Noi | Loro |\n|---|---|---|\n{rows}\n\n"
        f"## Lane\n| Lane | Noi | Loro | Early | Priorità |\n|---|---|---|---|---|\n{lane_rows}\n\n"
        f"## Piano rapido\n" + "\n".join(f"- {p}" for p in plan) + "\n\n"
        + (f"## Build: {build.label}\n" + "\n".join(f"- {a}" for a in [build.stats, *build.adaptations]) + "\n\n" if build else "")
        + (f"## Gameplan (LLM)\n{gameplan}\n" if gameplan else ""),
        encoding="utf-8",
    )
    return path


def draft_report(console: Console, state: DraftState, args) -> None:
    """Fine champ select: analisi di composizione e build. Ruoli e summoner nemici non sono ancora noti."""
    report = analyze(state.ally_champions, state.enemy_champions)
    me = state.me
    build = recommend_build(me.champion, state.ally_champions, state.enemy_champions, report) if me and me.champion else None
    console.print(render_final(state, report, [], build, title="Draft completa", meta=args.meta))
    console.print("[grey62]Ruoli e summoner nemici, analisi delle lane e gameplan arrivano a partita iniziata.[/]")


def game_report(console: Console, state: DraftState, args) -> None:
    """Partita iniziata: ruoli e summoner di tutti, lane, build e gameplan dell'LLM."""
    report = analyze(state.ally_champions, state.enemy_champions)
    lanes = lane_matchups(state, args.laning)
    me = state.me
    build = recommend_build(me.champion, state.ally_champions, state.enemy_champions, report) if me and me.champion else None
    console.print(render_final(state, report, lanes, build, meta=args.meta))
    plan = heuristic_plan(report, state, lanes)

    gameplan = ""
    if not args.no_llm:
        console.rule(f"[bold magenta]Gameplan · {args.model}")
        try:
            for chunk in stream_gameplan(build_prompt(state, report, lanes, build, args.meta), args.model, args.ollama_host):
                gameplan += chunk
                console.print(chunk, end="", markup=False, highlight=False)
            console.print()
        except OllamaError as e:
            console.print(f"[yellow]{e}[/] — resta valido il piano rapido qui sopra.")
    path = save_report(state, report, lanes, build, plan, gameplan)
    console.print(f"\n[grey62]Report salvato in {path}[/]")


def dump_raw(snapshot: GameSnapshot) -> None:
    """Salva la risposta grezza (una per fonte e partita): serve a verificare i campi che il gioco espone."""
    raw_dir = REPORTS_DIR / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{datetime.now():%Y-%m-%d_%H%M%S}_{snapshot.source}.json"
    path.write_text(json.dumps(snapshot.raw, ensure_ascii=False, indent=1), encoding="utf-8")


def current_game(sources: list, db: ChampionDB) -> GameSnapshot | None:
    for source in sources:
        snapshot = source.get_game_state(db)
        if snapshot:
            return snapshot
    return None


def run_live(lcu, game_sources: list, db: ChampionDB, args, console: Console) -> None:
    draft_done: tuple | None = None
    game_done: frozenset | None = None
    dumped: set[tuple] = set()
    live: Live | None = None

    def show(renderable) -> None:
        nonlocal live
        if live is None:
            live = Live(console=console, auto_refresh=False)
            live.start()
        live.update(renderable, refresh=True)

    def pause_live() -> None:
        nonlocal live
        if live is not None:
            live.stop()
            live = None

    try:
        while True:
            snapshot = current_game(game_sources, db)
            if snapshot:
                key = snapshot.key()
                if not args.demo and (key, snapshot.source) not in dumped:
                    dumped.add((key, snapshot.source))
                    dump_raw(snapshot)
                if snapshot.state is None:
                    show(waiting("Partita fuori da Summoner's Rift: nessuna analisi."))
                elif key != game_done:  # una sola analisi per partita, dalla prima fonte disponibile
                    game_done, draft_done = key, None
                    pause_live()
                    game_report(console, snapshot.state, args)
                    if args.demo:
                        return
                time.sleep(3)
                continue
            game_done = None

            try:
                session = lcu.get_session()
            except LCUNotRunning:
                show(waiting("In attesa del client di League of Legends..."))
                time.sleep(3)
                continue
            if session is None:
                show(waiting("Draft analizzata. In attesa dell'inizio della partita..." if draft_done
                             else "Client trovato. In attesa della champ select..."))
                time.sleep(args.interval)
                continue

            state = parse_session(session, db)
            if state.all_locked:
                if state.key() != draft_done:
                    draft_done = state.key()
                    pause_live()
                    draft_report(console, state, args)
                time.sleep(args.interval)  # resta sul report finché la champ select non finisce
                continue

            report = analyze(state.ally_champions, state.enemy_champions)
            show(render_live(state, report, compute_suggestions(state, db, args), args.meta))
            time.sleep(args.interval)
    finally:
        pause_live()


def manual_state(db: ChampionDB, allies: str, enemies: str, role: str) -> DraftState:
    """Draft scritta a mano: entrambe le squadre in ordine top, jungle, mid, adc, support.

    Le summoner si indicano dopo i due punti, es. "Viego:smite+flash".
    """

    def champions(names: str) -> list:
        out = []
        for entry in names.split(","):
            name, _, spells = entry.partition(":")
            champ = db.find(name.strip())
            if not champ:
                raise SystemExit(f"Campione sconosciuto: {name.strip()!r}")
            spell_ids = []
            for spell in filter(None, spells.split("+")):
                found = find_spell(spell)
                if not found:
                    raise SystemExit(f"Summoner sconosciuta: {spell.strip()!r}")
                spell_ids.append(found.id)
            out.append(with_spells(champ, spell_ids))
        return out

    ally_champs, enemy_champs = champions(allies), champions(enemies)
    ally_roles = ROLES[: len(ally_champs)] if len(ally_champs) == 5 else infer_roles(ally_champs)
    me_role = ROLE_ALIASES.get(role.lower(), "") if role else ""
    ally_slots = [Slot(i, r, c, is_me=(r == me_role)) for i, (r, c) in enumerate(zip(ally_roles, ally_champs))]
    if len(enemy_champs) == 5:
        enemy_slots = [Slot(5 + i, r, c) for i, (r, c) in enumerate(zip(ROLES, enemy_champs))]
    else:
        enemy_slots = [Slot(5 + i, "", c) for i, c in enumerate(enemy_champs)]
        assign_missing_roles(enemy_slots)
    return DraftState(ally_slots, enemy_slots)


def main(argv: list[str] | None = None) -> None:
    if not FROZEN:
        return run(argv)
    # Aperto con doppio clic la console si chiude all'uscita: la teniamo aperta per leggere l'ultimo messaggio.
    try:
        run(argv)
    except SystemExit as e:
        if e.code not in (None, 0):
            print(e.code if isinstance(e.code, str) else "")
    except Exception:
        import traceback

        traceback.print_exc()
    input("\nPremi Invio per chiudere...")


def run(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="lolpick", description="Assistente per la champ select di League of Legends.")
    p.add_argument("--demo", action="store_true", help="simula una champ select senza il client")
    p.add_argument("--demo-step", type=float, default=1.5, help="secondi tra un'azione e l'altra nella demo")
    p.add_argument("--analyze", nargs=2, metavar=("ALLEATI", "NEMICI"),
                   help='analizza una draft a mano, es. "Ornn:tp+flash,Viego:smite+flash,Ahri,Jinx,Thresh" "Jayce,Lee Sin,Xerath,Ezreal,Karma"')
    p.add_argument("--role", default="", help="il tuo ruolo con --analyze (top, jungle, mid, adc, support)")
    p.add_argument("--model", default=DEFAULT_MODEL, help=f"modello Ollama (default {DEFAULT_MODEL})")
    p.add_argument("--ollama-host", default=DEFAULT_HOST)
    p.add_argument("--no-llm", action="store_true", help="non chiamare Ollama, solo analisi a regole")
    p.add_argument("--interval", type=float, default=1.0, help="secondi tra un controllo e l'altro")
    p.add_argument("--meta-weight", type=float, default=META_WEIGHT,
                   help=f"peso della tier list nei pick consigliati, 0-1 (default {META_WEIGHT}; 0 = solo bisogni del team)")
    p.add_argument("--no-meta", action="store_true", help="ignora la tier list della patch")
    p.add_argument("--matchup-weight", type=float, default=MATCHUP_WEIGHT,
                   help=f"peso dei matchup nei pick consigliati, 0-1 (default {MATCHUP_WEIGHT}; 0 = ignorati)")
    p.add_argument("--lane-weight", type=float, default=LANE_WEIGHT,
                   help=f"peso della fase di lane (oro al minuto 15) nei pick consigliati, 0-1 (default {LANE_WEIGHT}; 0 = ignorata)")
    p.add_argument("--update", action="store_true", help="aggiorna lista campioni e tier list all'ultima patch")
    args = p.parse_args(argv)
    if args.update:
        from .update import main as update

        return update()

    console = Console()
    db = load_champions()
    if db.estimated:
        console.print(f"[grey50]Voti stimati (da rivedere in champions.csv): {', '.join(c.name for c in db.estimated)}[/]")
    args.meta = None if args.no_meta else load_meta()
    args.matchups = load_matchups() if args.matchup_weight > 0 else None
    args.laning = load_laning()  # serve anche alla priorità di lane, non solo ai pick
    if args.laning is None:
        console.print(f"[yellow]Nessun dato sulla fase di lane: lancia `{UPDATE_CMD}` per scaricarli.[/]")
    if args.matchup_weight > 0 and args.matchups is None:
        console.print(f"[yellow]Nessun dato sui matchup: lancia `{UPDATE_CMD}` per scaricarli.[/]")
    if not args.no_meta:
        if args.meta is None:
            console.print(f"[yellow]Nessuna tier list: lancia `{UPDATE_CMD}` per scaricare quella della patch.[/]")
        elif args.meta.age_days() > 14:  # una patch dura due settimane
            console.print(f"[yellow]Tier list della patch {args.meta.patch} aggiornata {args.meta.age_days()} giorni fa: "
                          f"lancia `{UPDATE_CMD}`.[/]")

    try:
        if args.analyze:
            game_report(console, manual_state(db, *args.analyze, args.role), args)
        elif args.demo:
            from .demo import DemoSource

            demo = DemoSource(db, args.demo_step)
            run_live(demo, [demo], db, args, console)
        else:
            lcu = LCUSource()
            run_live(lcu, [lcu, LiveGameSource()], db, args, console)
    except KeyboardInterrupt:
        console.print("\nCiao!")
