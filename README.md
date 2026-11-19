# modelscompare

Browser interattivo per i prezzi live dei modelli GitHub Copilot. Mostra chiaramente la distinzione di contesto (Default vs Long Context, soglie token) e di reasoning (`🧠 Reasoning` vs `Standard`/`N/D`), oltre ai 4 costi separati (input, cached read, cached write e output). Catalogo, prezzi e metadati di intelligenza sono letti dinamicamente a ogni avvio.

## Installazione

Richiede Python 3.10+ e una connessione Internet per caricare i prezzi live.

### Windows

Scarica `modelscompare-windows-x64.zip` dall'ultima release, estrai lo ZIP e avvia `ModelsCompare.exe`. Non serve installare Python.

### macOS e Linux

Scarica l'archivio della tua piattaforma (`modelscompare-macos-arm64.tar.gz`, `modelscompare-linux-x64.tar.gz`, …), estrailo e avvia il binario:

```sh
tar -xzf modelscompare-linux-x64.tar.gz
./modelscompare
```

Su macOS, se Gatekeeper blocca il binario non firmato, usa `Impostazioni di Sistema → Privacy e sicurezza → Apri comunque` oppure esegui `xattr -d com.apple.quarantine ./modelscompare` dopo aver verificato il checksum.

### Installa il comando `modelscompare` nel PATH

Dopo aver estratto l’archivio, installa il comando una sola volta:

```sh
# macOS / Linux
./modelscompare --install

# Windows PowerShell
.\ModelsCompare.exe --install
```

Poi apri un nuovo terminale e scrivi `modelscompare` da qualsiasi cartella:

```sh
modelscompare
```

- **macOS/Linux:** il binario viene copiato in `~/.local/share/modelscompare/modelscompare`, collegato come `~/.local/bin/modelscompare` e, se necessario, aggiunge `~/.local/bin` al profilo della shell (`~/.zshrc`, `~/.bashrc`, `~/.config/fish/config.fish` o `~/.profile`).
- **Windows:** il binario viene copiato in `%LOCALAPPDATA%\Programs\modelscompare\modelscompare.exe` e la cartella viene aggiunta al PATH utente.
- Con `--no-path-update` il comando viene installato senza modificare il PATH.
- Con `pipx install .` il comando viene già creato da `pipx`; `--install` lo rileva e non duplica nulla.

### Da sorgente o con pipx

```sh
python -m pip install -e ".[test]"
python -m modelscompare
# oppure
pipx install .
modelscompare
```

Al primo avvio il setup chiede solo quali modelli ti interessano e quali formati usare per l'export. Puoi selezionare più modelli e CSV, Excel o entrambi; nessuna selezione di modello significa mostrare tutti i modelli.

## Interfaccia e Navigazione

L'interfaccia a terminale offre un banner adattivo, badge di stato, tabella a colonne (`MODELLO`, `CONTESTO`, `TIPO`, `INPUT`, `CACHED-R`, `CACHED-W`, `OUTPUT`) e scheda dettagli del modello evidenziato. Il badge `v0.2.3` nel banner mostra sempre la versione installata; se è disponibile un aggiornamento diventa `v0.2.3 ↑ v0.2.4`. Su terminali stretti le colonne cached vengono nascoste e i dettagli restano disponibili nella scheda; nulla viene tagliato a metà carattere, anche con emoji.

| Tasto | Azione |
| --- | --- |
| `↑` / `↓` | Seleziona un modello |
| `Home` / `End` / `PgUp` / `PgDn` | Spostamento rapido |
| `Invio` | Apre la scheda Artificial Analysis del modello |
| `Spazio` | Aggiunge o rimuove il preferito (indicato con `★`) |
| `M` | Aggiunge o rimuove la variante di contesto selezionata dalla selezione di confronto (`✓`) |
| `C` | Attiva la modalità confronto; `Esc` torna all'elenco completo |
| `A` | Alterna tutti i modelli e i preferiti |
| `T` | Cambia unità live tra USD e crediti AI |
| `/` | Cerca per nome, provider, categoria, stato o soglia |
| `Esc` | Chiude l'aiuto, torna dalla vista confronto o pulisce la ricerca |
| `H` / `?` | Mostra/nasconde l'aiuto completo |
| `S` | Riapre il setup in qualsiasi momento |
| `E` | Avvia l'export, chiedendo conferma prima di scrivere file |
| `R` | Ricarica prezzi e catalogo live |
| `U` | Cerca aggiornamenti nelle release GitHub |
| `Q` / `Ctrl+C` | Esce |

Il banner mostra la posizione nel catalogo e l'unità attiva; la barra inferiore è contestuale e mostra il numero di modelli selezionati per il confronto.

## Filtri e comandi

```sh
modelscompare --provider OpenAI --category Powerful
modelscompare --model GPT --tier "Long context" --sort cached_read
modelscompare --status GA --threshold 272K --sort output --unit credits
modelscompare --search reasoning --search openai --limit 10
modelscompare --sort cached_write --limit 10 --no-export
modelscompare --json --sort cached_read > prezzi.json
modelscompare --setup
modelscompare --all-models
modelscompare --export --output-prefix confronto_settembre --output-dir ./report
```

Filtri testuali case-insensitive si applicano come sottostringhe. Sono disponibili:

- `--model`, `--provider`, `--category`, `--tier`, `--status`, `--threshold`: ripetibili; i termini dello stesso flag si combinano in OR.
- `--search`: cerca in nome, provider, categoria, tier, stato, soglia e metadati; termini diversi si combinano in AND.
- `--sort`: `name`, `provider`, `input`, `cached_read`, `cached_write`, `output`.
- `--unit`: `usd` o `credits`.
- `--limit`: numero massimo di modelli.
- `--json`: output JSON con i quattro costi separati, utile per script e pipeline.
- `--export` / `--no-export`: autorizza o disabilita l'export. Il flag esplicito `--export` autorizza l'export anche in batch; l'azione interattiva `E` chiede conferma. `--no-export` disabilita anche l'azione `E`.
- `--output-dir`: cartella di destinazione degli export (default: `Downloads`).
- `--version`: stampa la versione installata.
- `--install`: installa il comando `modelscompare` nel PATH dell’utente; `--no-path-update` evita di modificare il PATH.

`--clear-selection` cancella i preferiti salvati e `--config-file` seleziona un profilo diverso.

## Export

Nell'app interattiva i file vengono creati solo dopo conferma e salvati nella cartella `Downloads` o in `--output-dir`; il flag `--export` è il consenso esplicito per l'esecuzione batch. Se il nome esiste già, o un file è aperto in Excel, viene scelto il primo nome libero aggiungendo `_1`, `_2` e così via senza sovrascrivere gli export precedenti.

- CSV: UTF-8 con BOM per aprire correttamente i caratteri accentati in Excel.
- Excel: filtri automatici, intestazione congelata, formati numerici, link ai modelli, righe leggibili e larghezze calcolate.
- Ogni costo esporta colonne numeriche separate per USD e crediti AI per milione di token. Nessuna colonna combina costi diversi.
- Prima di scrivere il file Excel, il workbook viene riaperto e validato; i caratteri di controllo non validi per XML vengono rimossi e i file parziali non vengono mai lasciati sul disco.
- Se l'export fallisce, il file temporaneo viene eliminato e l'errore viene riportato senza corrompere l'export precedente.

## Aggiornamenti

All'avvio, nelle build standalone (EXE Windows, binari macOS/Linux), l'app controlla le release del repository pubblico. Se è disponibile una versione più recente:

- con `auto_update` attivo (default) il pacchetto viene scaricato, verificato con SHA-256 e installato al riavvio;
- in alternativa compare un badge `↑ versione` e puoi usare il tasto `U` o il comando `modelscompare --update`.

I dati utente non vengono mai toccati: preferiti, filtri ed export restano nella cartella di configurazione dell'utente, separata dal binario. Le installazioni da sorgente/pip non vengono modificate automaticamente; ricevono le istruzioni per `pip`/`pipx`.

```sh
modelscompare --check-updates
modelscompare --check-updates --json
modelscompare --update
modelscompare --no-update-check
modelscompare --no-auto-update
```

Le release pubblicano anche file `.sha256`; l'updater verifica il digest SHA-256 fornito dall'API GitHub o dal file di checksum prima di installare. Il repository predefinito è `Chuucommie/modelscompare`; per un fork imposta `MODELSCOMPARE_REPO=owner/repo`. Le variabili `MODELSCOMPARE_DOWNLOAD_DIR` e `MODELSCOMPARE_UPDATE_DIR` permettono di usare cartelle diverse.

## Percorsi per piattaforma

| Piattaforma | Configurazione | Export predefinito | Cache aggiornamenti |
| --- | --- | --- | --- |
| Windows | `%APPDATA%\modelscompare\config.json` | `%USERPROFILE%\Downloads` | `%LOCALAPPDATA%\modelscompare\updates` |
| macOS | `~/.config/modelscompare/config.json` | `~/Downloads` | `~/Library/Caches/modelscompare/updates` |
| Linux | `$XDG_CONFIG_HOME/modelscompare/config.json` (default `~/.config`) | `XDG_DOWNLOAD_DIR` o `~/Downloads` | `$XDG_CACHE_HOME/modelscompare/updates` (default `~/.cache`) |

`--setup` riapre il wizard; `--config-file` seleziona un profilo diverso. Le selezioni precedenti in `selection.json` sono migrate automaticamente.

## Distribuzione e release

La build è multipiattaforma e avviene su GitHub Actions su Windows, macOS e Linux:

```sh
python scripts/build_release.py
# Windows, alternativa:
.\package-windows.ps1
```

Gli asset pubblicati sono:

- `ModelsCompare.exe` e `modelscompare-windows-x64.zip`
- `modelscompare-linux-<arch>.tar.gz`
- `modelscompare-macos-<arch>.tar.gz`
- un file `.sha256` per ogni archivio

Gli eseguibili non sono firmati digitalmente. Windows può mostrare SmartScreen; macOS può richiedere l'apertura esplicita la prima volta. Per una release, crea e invia un tag:

```sh
git tag v0.2.3
git push origin v0.2.3
```

Il workflow esegue prima i test su tutti e tre i sistemi, poi compila e allega gli asset alla release. Il piano dettagliato per macOS/Linux, terminali supportati, rischi e prossimi passi è in [`docs/PLATFORMS.md`](docs/PLATFORMS.md).

## Sorgenti e test

Il parser riconosce dinamicamente le tabelle di prezzo e i valori AI-credit/USD pubblicati; non mantiene elenchi statici di modelli. Le richieste di rete e il parsing sono separati per consentire test con fixture.

- https://docs.github.com/copilot/reference/copilot-billing/models-and-pricing
- https://artificialanalysis.ai/models

```sh
python -m pytest
```
