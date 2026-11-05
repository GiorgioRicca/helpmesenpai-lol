# lolpick

Assistente per la champ select di League of Legends.

- **Durante la draft:** confronta le due composizioni su 18 criteri e suggerisce i pick migliori per te e per i compagni che non hanno ancora lockato, bilanciando ciò che serve al team, la forza del campione nel meta della patch, i matchup contro i nemici già scelti e la fase di lane (dati Diamond+).
- **A draft completa:** mostra i punti forti e deboli di entrambe le squadre, l'archetipo di build consigliato e un piano rapido a regole.
- **Dalla schermata di caricamento:** solo ora si conoscono ruoli e summoner degli avversari (in champ select sono nascosti). L'app li legge dal client (`/lol-gameflow/v1/session`), oppure dalla Live Client Data API se la avvii a partita già iniziata. Poi analizza le lane e chiede a un modello locale su Ollama il gameplan completo: win condition, piano per il tuo ruolo e build.

L'app legge solo le API locali del client (LCU) e della partita (Live Client Data, porta 2999), in sola lettura. Di ogni partita salva i dati grezzi in `reports/raw/`, utili per verificare quali campi espone il gioco. Contengono i Riot ID dei giocatori e restano sul tuo computer. Non tocca il gioco, non lo modifica e non fa pick o ban al posto tuo.

## Avvio

```bash
uv sync
uv run lolpick-update       # dati dell'ultima patch: lista campioni, tier list, matchup e fase di lane Diamond+
uv run lolpick              # live: avvialo con il client aperto
uv run lolpick --demo       # champ select simulata, senza client
uv run lolpick --analyze "Ornn:tp+flash,Viego:smite+flash,Ahri:ignite+flash,Jinx:heal+flash,Thresh:ignite+flash" "Jayce,Lee Sin,Xerath,Ezreal,Karma" --role jungle
```

Tier list, matchup e fase di lane non sono nel repository: dopo aver clonato il progetto vanno scaricati con `lolpick-update`. I dati della patch sono statici: l'app legge solo i file in `lolpick/data/`, e li aggiorna solo `lolpick-update`. Lancialo a ogni nuova patch (ogni due settimane), o qualche giorno dopo per avere più partite. Se la tier list ha più di 14 giorni, l'app te lo ricorda all'avvio. Lo script mostra anche i campioni nuovi senza voti in `champions.csv`.

In `--analyze` entrambe le squadre vanno in ordine top, jungle, mid, adc, support. Le summoner sono facoltative e si scrivono dopo i due punti, in inglese o in italiano (`ignite`/`ustione`, `tp`, `exhaust`/`sfinimento`, ecc.).

Per il gameplan con l'LLM:

```bash
ollama serve
ollama pull richardyoung/qwen3.6-27b-abliterated   # default (~16 GB, ~3 min a gameplan)
# più veloce ma meno preciso: --model qwen3:8b (oppure env LOLPICK_MODEL)
```

Se Ollama non è attivo, l'app mostra comunque l'analisi e il piano a regole. Ogni report finale viene salvato in `reports/` in formato Markdown. Con `--no-llm` salti la chiamata a Ollama.

## Eseguibile Windows

PyInstaller non fa cross-compilazione: il `.exe` va creato su Windows. Su un PC Windows con [uv](https://docs.astral.sh/uv/) installato, dalla cartella del progetto:

```bash
uv run --with pyinstaller python packaging/build.py
```

In alternativa, dopo aver caricato il progetto su GitHub, lancia a mano il workflow *Eseguibile Windows* (`.github/workflows/windows.yml`) dalla scheda Actions, oppure crea un tag `v*`: lo zip si scarica dagli artifact della run.

Il risultato è la cartella `dist/lolpick/` (c'è anche lo zip):

- `lolpick.exe`: un solo file, Python non serve. Con doppio clic parte in modalità live; accetta gli stessi flag di `uv run lolpick`, più `--update`, che fa quello che fa `lolpick-update`.
- `Aggiorna dati patch.bat`, `Demo.bat`, `Senza LLM.bat`: scorciatoie da aprire con doppio clic.
- `data/`: `champions.csv`, lista campioni e tier list. Si modifica e si aggiorna qui, non dentro l'exe. Se la cancelli, l'exe la ricrea con i dati che aveva al momento della build.
- `reports/`: la crea l'exe accanto a sé.

All'uscita, o in caso di errore, la finestra aspetta Invio prima di chiudersi. Ollama va installato e avviato a parte. Windows SmartScreen può segnalare l'exe perché non è firmato: *Ulteriori informazioni → Esegui comunque*.

## Come funziona

| File | Cosa fa |
|---|---|
| `lolpick/data/champions.csv` | voti 1-10 per campione su ogni criterio, più i ruoli (il primo è quello principale) |
| `lolpick/lcu.py` | trova porta e password del client e legge `/lol-champ-select/v1/session` |
| `lolpick/draft.py` | pick lockati, hover, ban e ruoli degli alleati durante la champ select |
| `lolpick/livegame.py` | a partita iniziata: ruoli e summoner di tutti e dieci i giocatori |
| `lolpick/builds.py` | archetipo di build (colonna `build` del CSV) e adattamenti: difese, anti-heal, penetrazione, tenacità |
| `lolpick/analysis.py` | profilo di squadra, archetipi, punti forti e deboli, suggerimenti di pick |
| `lolpick/summoners.py` | summoner spell e il loro effetto sui voti (es. Ignite: più early e burst) |
| `lolpick/lanes.py` | matchup lane per lane, con la bot lane come 2v2: pressione early, facilità di gank, esposizione ai gank nemici |
| `lolpick/roleplan.py` | decisioni concrete per il tuo ruolo: dove gankare e il pathing, dove roamare, fight o split, lane o roaming, come fightare da ADC |
| `lolpick/prompts.py` | prompt per l'LLM: voti di ogni campione, lane e istruzioni diverse per ogni ruolo |
| `lolpick/llm.py` | streaming del gameplan da Ollama |
| `lolpick/meta.py` | tier list della patch: forza nel meta di ogni campione per lane |
| `lolpick/matchups.py` | win rate di ogni campione contro ogni avversario, per coppia di lane |
| `lolpick/laning.py` | fase di lane: distacco d'oro al minuto 15 per ogni matchup nella stessa lane |
| `lolpick/update.py` | `lolpick-update`: scarica Data Dragon e le statistiche della patch in `data/ddragon_cache.json` e `data/meta.json` |

**Profilo di squadra.** È la media dei voti. Per engage, disengage, tank, waveclear e splitpush basta un campione forte, quindi pesa di più il migliore (60% massimo, 40% media).

**Suggerimenti.** Ogni candidato viene aggiunto ai pick già lockati e la squadra risultante riceve un punteggio. Il punteggio premia la copertura dei criteri (con rendimenti decrescenti oltre il 7), penalizza i danni sbilanciati oltre 70/30, dà un bonus a un'identità chiara (dive, teamfight, poke, ecc.) e alza il peso delle risposte ai punti forti nemici. Per esempio, se loro hanno molto engage, il disengage vale di più. Le regole sono in `COUNTERS` e `BASE_WEIGHTS`.

**Tier list.** Per ogni lane ci sono pick rate, win rate e numero di partite in Diamond+ (ranked solo/duo, tutte le regioni), dalle statistiche aggregate di Lolalytics.
1. Il win rate viene "smussato" verso la media della lane con 3000 partite virtuali, così un 58% su 300 partite non batte un 53% su 100.000.
2. Il punteggio è win rate smussato meno media della lane, più 0,75 × log₂(pick rate). Ogni raddoppio del pick rate vale quanto 0,75 punti di win rate: tanti pick indicano un campione forte e affidabile.
3. Il punteggio diventa uno z-score rispetto ai campioni giocati davvero in quella lane (pick rate ≥ 0,5%), e lo z-score dà il tier: S+ ≥ 1,5, S ≥ 0,8, A ≥ 0,25, B ≥ −0,4, C ≥ −1, poi D.

A inizio patch, se una lane ha meno di 150.000 partite, si usano i dati della patch precedente. Le costanti sono in cima a `meta.py`.

**Pick consigliati con il meta.** Per ogni candidato si calcolano due z-score: quanto copre i bisogni del team (il punteggio sopra) e quanto è forte nel meta della lane. Il punteggio finale è `0,6 × team + 0,4 × meta`. Con `--meta-weight` cambi il peso (0 = solo team, 1 = solo tier list), con `--no-meta` ignori la tier list. Oltre ai ruoli del CSV, diventano candidati anche i campioni giocati in quella lane da almeno lo 0,5% delle partite (es. Yasuo ADC). Accanto ai pick consigliati e ai campioni delle due squadre compaiono tier, win rate e partite in quel ruolo (es. `S  53.6% 53k`); il tier va anche nel prompt dell'LLM. Il win rate è quello grezzo di Lolalytics, in media circa il 52% in Diamond+, quindi va confrontato con gli altri campioni e non con il 50%.

**Matchup.** `lolpick-update` scarica da Lolalytics tutte le combinazioni campione/lane contro lane avversaria (circa 2.000 richieste e 1-2 minuti, 4 alla volta) in `data/matchups.json`. Sono esclusi i campioni con meno di 1.000 partite in quella lane, per cui la fonte non ha dati. Se fallisce più del 5% delle richieste restano i dati precedenti.

Per ogni nemico già lockato il vantaggio è il win rate contro di lui meno il win rate medio del campione contro quella lane: così conta solo l'incrocio tra i due, perché la forza generale è già nella tier list. Il vantaggio è smussato con `n/(n+400)` e pesato per lane: 1 per l'avversario diretto, 0,6 tra ADC e support, 0,25 per le altre lane, e ×0,7 se il ruolo del nemico è dedotto. La somma dei vantaggi, divisa per 2 punti e limitata a ±3, pesa il 30% del punteggio finale: `0,7 × (team e meta) + 0,3 × matchup`. Con `--matchup-weight` cambi il peso (0 = ignorati). Finché non si conoscono nemici i matchup non contano, e più nemici sono noti più pesano.

**Fase di lane.** Nessuna fonte trovata pubblica la percentuale di lane vinte: la misura più vicina è il distacco d'oro al minuto 15 (GD15), che `lolpick-update` scarica da U.GG in `data/laning.json` (Diamond+, tutte le regioni; 173 file da circa 1 MB, meno di un minuto). Per ogni candidato si usa il GD15 contro l'avversario di lane se è noto, altrimenti la sua media nella lane. Con poche partite il valore contro l'avversario tende alla media (`n/(n+50)`), e se il ruolo del nemico è dedotto conta il 70%. Il GD15 diventa uno z-score sui candidati e pesa il 35% del punteggio finale, la voce singola più pesante: `0,65 × resto + 0,35 × fase di lane`. Con `--lane-weight` cambi il peso (0 = ignorata). I dati coprono solo la stessa lane, quindi non c'è ADC contro support. Il GD15 penalizza i campioni che scalano, come Kayle o Smolder, anche quando vincono la partita.

**Ordine dei pick.** Sono consigliati 10 campioni per slot, in ordine di punteggio finale e non di win rate: per questo un pick con win rate più basso può stare sopra. La tabella delle lane mostra anche il GD15 atteso. Per il tuo slot ogni riga mostra anche il GD15 atteso (es. `GD15 +340`), il matchup più influente (es. `vs Yasuo 58.5% 2.2k`) e i motivi. I compagni hanno una griglia compatta con tier, win rate, partite e GD15. I matchup non vanno nel prompt dell'LLM.

**Lane.** Pressione early è la forza nella fase di lane, soprattutto early game, poi burst, poke, sustain e CC. Facilità di gank combina il setup degli alleati in lane (CC, early game, burst, quindi conta anche Ignite) con la vulnerabilità degli avversari (poca mobilità, fragilità, poco disengage). La bot lane è valutata come duo.

**Range.** È la portata effettiva delle minacce, abilità comprese, non solo degli auto attack: Xerath 10, Ziggs e Varus 9, Caitlyn ed Ezreal 8, Ahri 7, Garen e Master Yi 1. Entra nella priorità di lane, nel confronto tra ADC e nell'archetipo poke.

**Priorità di lane.** Dice chi spinge e si muove per primo. Parte dai voti, 0,4 early game + 0,3 waveclear + 0,15 CC + 0,15 range come media dei campioni in lane, e aggiunge il distacco d'oro atteso al minuto 15 nel matchup: ogni 250 oro spostano di un punto lo scarto tra le squadre. Così i dati reali pesano più dei voti, senza ignorare waveclear e range. In bot si sommano ADC e support; con ruoli nemici dedotti il distacco conta il 70%. Uno scarto di almeno 0,6 dà la priorità a una squadra, altrimenti è contesa. Senza dati restano solo i voti. L'esito early della lane (`favorevole`/`sfavorevole`) resta invece basato solo sui voti.

**Decisioni per ruolo** (`roleplan.py`, a partita iniziata, quando i ruoli di tutti sono noti):
- **Jungle:** la lane da gankare (facilità di gank più un bonus se gli avversari hanno priorità e spingono). Ogni scelta riporta i motivi: Ignite, CC, escape, fragilità. Poi il pathing: si parte dal lato opposto alla lane da gankare e si chiude il clear lì, al livello 3-4. Seguono invade, dove andrà il loro jungler e gank o farm in base al tempo della composizione.
- **Mid:** chi ha la priorità in mid e su quale side lane conviene roamare, e quando.
- **Top:** teamfight, split push o mirror, e chi gestisce il loro splitpusher se ti batte in 1v1 (tu, il mid che mirrora, oppure in due con il jungler). In più se chiamare il jungler e l'uso del Teleport.
- **Support:** lane o roaming e quando (early o dal mid game), chi peelare e da quali minacce, engage o peel.
- **ADC:** range rispetto al loro ADC, come impostare il 2v2 e come fightare. Le opzioni sono sopravvivere all'engage e rispondere, poke, pick, front to back o kite.

Queste decisioni compaiono nel piano rapido e vanno nel prompt come punto di partenza.

**Prompt.** Il modello riceve i voti di ogni campione, la tabella delle lane, il "tempo" della composizione e istruzioni diverse a seconda del tuo ruolo. Per esempio il jungler riceve percorso, lane da gankare o proteggere e bilanciamento tra gank e farm. Gli è chiesto di non parlare delle singole abilità, perché i modelli locali tendono a inventarle. Gli è anche vietato dare consigli generici ("non morire", "farma bene"): ogni punto deve nominare lane, campioni e momento della partita, partendo dalle decisioni calcolate.

## Personalizzare i voti

Modifica `lolpick/data/champions.csv` (si apre anche con Excel o Numbers). Se lo modifichi con Numbers, salvalo con File > Esporta come > CSV: con Cmd+S Numbers sovrascrive il file con un documento `.numbers`, e l'app non riesce più a leggerlo. Il CSV esportato può usare `;` o `,` come separatore e avere il nome della tabella come prima riga: l'app gestisce entrambi i casi. La colonna `champion` accetta sia l'id Data Dragon sia il nome (es. `MonkeyKing` o `Wukong`). Una nota nella colonna `note` segna i voti come stima da rivedere. I campioni usciti dopo e assenti dal CSV ricevono un profilo stimato dai tag di Data Dragon, e l'app lo segnala all'avvio.

## Limiti attuali

- I matchup entrano nei pick consigliati ma non nella valutazione delle lane, che resta basata sui voti.
- Tier list e matchup usano endpoint pubblici ma non documentati di Lolalytics, la fase di lane quelli di U.GG: se cambiano formato, `lolpick-update` fallisce e restano i dati precedenti.
- In blind pick i nemici restano nascosti fino al caricamento, quindi il report finale non parte.
- Ruoli e summoner nemici si conoscono solo dalla schermata di caricamento. Per i ruoli l'app usa, in ordine: la posizione esplicita, se il gioco la fornisce; poi l'ordine della schermata di caricamento (top, jungle, mid, adc, support), accettato solo se chi ha Smite è secondo; infine la deduzione da Smite e campioni, segnata con `*`.
- Con il modello 27b il gameplan arriva circa 3 minuti dopo l'inizio del caricamento, e Ollama sullo stesso Mac del gioco può abbassare gli FPS. In alternativa puoi usare `--model qwen3:8b` oppure Ollama su un altro computer con `--ollama-host`.
- La build è un archetipo, non una lista di oggetti: gli oggetti cambiano a ogni patch.
