# ModelsCompare

Browser da terminale per i prezzi live dei modelli GitHub Copilot, con quattro costi separati (input, cached read, cached write, output) e metadati di reasoning/contesto.

## Avvio

- **Windows:** doppio clic su `ModelsCompare.exe` oppure eseguilo da PowerShell.
- **macOS / Linux:** apri il terminale nella cartella estratta ed esegui `./modelscompare`.

Serve una connessione Internet per caricare i prezzi live.

## Comandi utili

- Frecce per navigare; `Invio` apre la scheda Artificial Analysis.
- `Spazio` aggiunge/rimuove un preferito; `M` seleziona per il confronto; `C` mostra il confronto.
- `T` cambia unità USD/crediti; `/` cerca; `H` mostra l'aiuto completo.
- `U` cerca aggiornamenti; `Q` esce.

La configurazione personale (preferiti, filtri, formati di export) è salvata nel profilo utente e non viene toccata dagli aggiornamenti. Gli export finiscono in `Downloads` oppure nella cartella scelta con `--output-dir`.

## Aggiornamenti

Le build standalone controllano le release GitHub all'avvio e possono aggiornarsi automaticamente preservando i dati. Ogni archivio è accompagnato da un file `.sha256`; l'app verifica il checksum prima di installare.

Gli eseguibili non sono firmati digitalmente: Windows può mostrare un avviso SmartScreen e macOS può richiedere l'apertura esplicita la prima volta.
