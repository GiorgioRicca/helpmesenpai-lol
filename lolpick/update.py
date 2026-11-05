"""`uv run lolpick-update`: porta i dati statici all'ultima patch (lista campioni + tier list Diamond+)."""

from __future__ import annotations

import sys

import requests
from rich.console import Console
from rich.progress import Progress

from .champions import DDRAGON_CACHE, load_champions, update_ddragon
from .criteria import ROLE_LABELS
from .laning import LANING_FILE, update_laning
from .matchups import MATCHUPS_FILE, update_matchups
from .meta import META_FILE, update_meta


def main() -> None:
    console = Console()
    try:
        dd = update_ddragon()
        console.print(f"Data Dragon {dd['version']}: {len(dd['champions'])} campioni → {DDRAGON_CACHE.name}")
        db = load_champions()
        meta = update_meta(db.version)
    except (requests.RequestException, ValueError, KeyError) as e:
        console.print(f"[red]Aggiornamento fallito, restano i dati precedenti: {e}[/]")
        sys.exit(1)

    note = "" if meta.patch == ".".join(db.version.split(".")[:2]) else " (patch nuova con poche partite: dati della precedente)"
    console.print(f"Tier list patch {meta.patch}{note} → {META_FILE.name}")
    for role, stats in meta.lanes.items():
        top = sorted(stats.items(), key=lambda kv: kv[1].z, reverse=True)[:5]
        names = ", ".join(f"{db.by_key(k).name if db.by_key(k) else k} {s.tier}" for k, s in top)
        console.print(f"  {ROLE_LABELS[role]:<8} {names}")
    try:
        with Progress(console=console, transient=True) as bar:
            task = bar.add_task("Matchup", total=None)
            matchups = update_matchups(db, meta, lambda done, total: bar.update(task, completed=done, total=total))
    except (requests.RequestException, ValueError, KeyError, OSError) as e:
        console.print(f"[red]Matchup non aggiornati, restano i dati precedenti: {e}[/]")
        sys.exit(1)
    pairs = sum(len(rows) for lane in matchups.data.values() for vs in lane.values() for rows in vs.values())
    console.print(f"Matchup patch {matchups.patch}: {pairs} coppie → {MATCHUPS_FILE.name}"
                  + (f" [yellow]({matchups.failed} richieste fallite)[/]" if matchups.failed else ""))
    try:
        with Progress(console=console, transient=True) as bar:
            task = bar.add_task("Fase di lane", total=None)
            laning = update_laning(db, meta.patch, lambda done, total: bar.update(task, completed=done, total=total))
    except (requests.RequestException, ValueError, KeyError, OSError) as e:
        console.print(f"[red]Fase di lane non aggiornata, restano i dati precedenti: {e}[/]")
        sys.exit(1)
    console.print(f"Fase di lane patch {laning.patch}: {sum(len(v) for v in laning.data.values())} campioni per lane → {LANING_FILE.name}"
                  + (f" [yellow]({laning.failed} richieste fallite)[/]" if laning.failed else ""))
    if db.estimated:
        console.print(f"[yellow]Campioni senza voti in champions.csv (stimati dai tag): {', '.join(c.name for c in db.estimated)}[/]")


if __name__ == "__main__":
    main()
