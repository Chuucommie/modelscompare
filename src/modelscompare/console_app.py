import argparse
import json
import math
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import webbrowser

import requests
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from modelscompare import __version__
from modelscompare.artificial_analysis import fetch_model_metadata, match_model_metadata
from modelscompare.exporting import export_records
from modelscompare.models import ModelPricing
from modelscompare.paths import default_downloads_dir
from modelscompare.preferences import Preferences, default_preferences_path, load_preferences, save_preferences
from modelscompare.pricing import fetch_pricing_page, parse_pricing_html
from modelscompare.setup_wizard import run_setup_wizard
from modelscompare.terminal_ui import ModelBrowser
from modelscompare.updates import (
    UpdateError,
    UpdateInfo,
    check_for_update,
    install_update,
    is_frozen,
)

EXAMPLES = """\
esempi:
  modelscompare --provider OpenAI --sort output
  modelscompare --search reasoning --unit credits --limit 10
  modelscompare --json --sort cached_read > prezzi.json
  modelscompare --export --output-dir ./report
  modelscompare --check-updates
  modelscompare --update
"""


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="modelscompare",
        description="Esplora i prezzi live GitHub Copilot con frecce e Invio.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--setup", action="store_true", help="riapre il wizard di configurazione")
    parser.add_argument("--all-models", action="store_true", help="mostra tutti i modelli, ignorando i preferiti nella vista iniziale")
    parser.add_argument("--clear-selection", action="store_true", help="cancella i modelli preferiti salvati")
    parser.add_argument("--config-file", type=Path, help="percorso alternativo del file di configurazione")
    parser.add_argument("--selection-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--model", action="append", default=[], metavar="TESTO", help="filtra il nome per sottostringa; ripetibile")
    parser.add_argument("--search", action="append", default=[], metavar="TESTO", help="cerca in nome, provider, categoria, tier, stato e soglia; ripetibile e combinabile con AND")
    parser.add_argument("--provider", action="append", default=[], metavar="TESTO", help="filtra provider per sottostringa; ripetibile")
    parser.add_argument("--category", action="append", default=[], metavar="TESTO", help="filtra categoria per sottostringa; ripetibile")
    parser.add_argument("--tier", action="append", default=[], metavar="TESTO", help="filtra tier per sottostringa; ripetibile")
    parser.add_argument("--status", action="append", default=[], metavar="TESTO", help="filtra stato di rilascio; ripetibile")
    parser.add_argument("--threshold", action="append", default=[], metavar="TESTO", help="filtra soglia di contesto; ripetibile")
    parser.add_argument(
        "--sort",
        choices=("name", "provider", "input", "cached_read", "cached_write", "output"),
        help="ordinamento",
    )
    parser.add_argument("--unit", choices=("usd", "credits"), help="unità costi per questa esecuzione")
    parser.add_argument("--limit", type=int, help="limita il numero di modelli mostrati")
    parser.add_argument("--json", action="store_true", help="stampa i modelli in JSON invece della tabella")
    export_group = parser.add_mutually_exclusive_group()
    export_group.add_argument("--export", action="store_true", help="esporta subito nei formati configurati")
    export_group.add_argument("--no-export", action="store_true", help="disabilita export anche dal tasto E")
    parser.add_argument("--output-prefix", default="copilot_models", help="nome base dei file (default: copilot_models)")
    parser.add_argument("--output-dir", type=Path, help="cartella degli export (default: Downloads)")
    update_group = parser.add_mutually_exclusive_group()
    update_group.add_argument("--check-updates", action="store_true", help="controlla le release GitHub e esce")
    update_group.add_argument("--update", action="store_true", help="scarica e installa l'ultima release")
    parser.add_argument("--no-update-check", action="store_true", help="non controllare gli aggiornamenti all'avvio")
    parser.add_argument("--no-auto-update", action="store_true", help="avvisa degli aggiornamenti ma non installarli automaticamente")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit deve essere almeno 1")
    return args


def _matches(value: str, preferred: list[str], overrides: list[str]) -> bool:
    folded = value.casefold()
    if preferred and folded not in {choice.casefold() for choice in preferred}:
        return False
    return not overrides or any(query.casefold() in folded for query in overrides)


def _matches_search(record: ModelPricing, queries: list[str]) -> bool:
    if not queries:
        return True
    haystack = " ".join(
        (
            record.name,
            record.provider,
            record.category,
            record.tier,
            record.release_status,
            record.threshold,
            record.context_window,
            record.reasoning_label,
        )
    ).casefold()
    return all(query.casefold() in haystack for query in queries)


def _sort_records(records: list[ModelPricing], sort_by: str, display_unit: str) -> list[ModelPricing]:
    attribute_by_sort = {
        "input": "input_cost",
        "cached_read": "cached_input_cost",
        "cached_write": "cache_write_cost",
        "output": "output_cost",
    }
    if sort_by in {"name", "provider"}:
        return sorted(records, key=lambda record: (getattr(record, sort_by).casefold(), record.name.casefold()))

    field = attribute_by_sort[sort_by]
    attribute = f"{field}_usd" if display_unit == "usd" else f"{field}_credits"
    return sorted(
        records,
        key=lambda record: (
            getattr(record, attribute) is None,
            getattr(record, attribute) if getattr(record, attribute) is not None else Decimal("Infinity"),
            record.name.casefold(),
        ),
    )


def filter_records(
    records: list[ModelPricing],
    preferences: Preferences,
    args: argparse.Namespace,
    *,
    include_saved_filters: bool = True,
) -> list[ModelPricing]:
    providers = preferences.providers if include_saved_filters else []
    categories = preferences.categories if include_saved_filters else []
    tiers = preferences.tiers if include_saved_filters else []
    statuses = preferences.release_statuses if include_saved_filters else []
    thresholds = preferences.thresholds if include_saved_filters else []
    search_queries = getattr(args, "search", [])
    selected = [
        record for record in records
        if _matches(record.name, [], args.model)
        and _matches(record.provider, providers, args.provider)
        and _matches(record.category, categories, args.category)
        and _matches(record.tier, tiers, args.tier)
        and _matches(record.release_status, statuses, args.status)
        and _matches(record.threshold, thresholds, args.threshold)
        and _matches_search(record, search_queries)
    ]
    sorted_records = _sort_records(selected, args.sort or preferences.sort_by, args.unit or preferences.display_unit)
    return sorted_records[:args.limit] if args.limit is not None else sorted_records


def fetch_live_records() -> list[ModelPricing]:
    records = parse_pricing_html(fetch_pricing_page())
    try:
        metadata = match_model_metadata([record.name for record in records], fetch_model_metadata())
    except Exception:
        metadata = {}

    enriched: list[ModelPricing] = []
    for record in records:
        match = metadata.get(record.name)
        matched = bool(match and "/models/" in match.url and "?search=" not in match.url)
        enriched.append(
            replace(
                record,
                artificial_analysis_url=match.url if match else "",
                is_reasoning=match.is_reasoning if matched else False,
                reasoning_known=matched,
                context_window=match.context_window if matched else "",
            )
        )
    return enriched


def _export_with_consent(
    records: list[ModelPricing],
    preferences: Preferences,
    prefix: str,
    console: Console,
    *,
    confirmed: bool = False,
    output_dir: Path | None = None,
) -> list[Path] | None:
    if not preferences.export_formats:
        console.print("Nessun formato export selezionato. Usa S per il setup.", style="yellow")
        return None
    destination = output_dir or default_downloads_dir()
    if not confirmed and not Confirm.ask(
        f"Esportare i costi selezionati in {destination}?",
        default=False,
        console=console,
    ):
        return []
    try:
        if output_dir is None:
            paths = export_records(records, preferences.export_formats, prefix)
        else:
            paths = export_records(records, preferences.export_formats, prefix, downloads_dir=output_dir)
    except (OSError, ValueError) as error:
        console.print(f"Export non riuscito: {error}", style="red")
        return None
    if paths:
        console.print("File salvati in " + str(paths[0].parent) + ": " + ", ".join(path.name for path in paths), style="green")
    return paths


def _plain_output(records: list[ModelPricing], unit: str) -> None:
    console = Console()
    if not records:
        console.print("Nessun modello corrisponde ai filtri richiesti.", style="yellow")
        return
    unit_label = "USD / 1M token" if unit == "usd" else "crediti AI / 1M token"
    table = Table(title=f"Modelli GitHub Copilot | prezzi live | {unit_label}", box=None, header_style="bold cyan")
    table.add_column("Modello")
    table.add_column("Contesto")
    table.add_column("Tipo")
    table.add_column("Input", justify="right")
    table.add_column("Cached read", justify="right")
    table.add_column("Cached write", justify="right")
    table.add_column("Output", justify="right")

    def value(record: ModelPricing, kind: str) -> str:
        attribute = f"{kind}_cost_usd" if unit == "usd" else f"{kind}_cost_credits"
        if kind == "cached_read":
            attribute = "cached_input_cost_usd" if unit == "usd" else "cached_input_cost_credits"
        cost = getattr(record, attribute)
        if cost is None:
            return "N/D"
        if unit == "usd":
            if abs(cost) >= Decimal("0.0001"):
                decimals = 4
            elif abs(cost) >= Decimal("0.000001"):
                decimals = 6
            else:
                decimals = 8
            return f"${cost:,.{decimals}f}"
        if abs(cost) >= Decimal("0.01"):
            decimals = 2
        elif abs(cost) >= Decimal("0.0001"):
            decimals = 4
        else:
            decimals = 6
        return f"{cost:,.{decimals}f} cr"

    for record in records:
        table.add_row(
            record.name,
            record.context_label,
            record.reasoning_label,
            value(record, "input"),
            value(record, "cached_read"),
            value(record, "cache_write"),
            value(record, "output"),
        )
    console.print(table)


def _json_cost(value: Decimal | None) -> float | None:
    if value is None or not value.is_finite():
        return None
    try:
        converted = float(value)
    except (OverflowError, ValueError):
        return None
    return converted if math.isfinite(converted) else None


def _record_payload(record: ModelPricing) -> dict[str, object]:
    return {
        "name": record.name,
        "provider": record.provider,
        "category": record.category,
        "release_status": record.release_status,
        "tier": record.tier,
        "threshold": record.threshold,
        "context": record.context_label,
        "reasoning": record.reasoning_label,
        "context_window": record.context_window,
        "artificial_analysis_url": record.artificial_analysis_url,
        "input_cost_usd": _json_cost(record.input_cost_usd),
        "cached_read_cost_usd": _json_cost(record.cached_input_cost_usd),
        "cached_write_cost_usd": _json_cost(record.cache_write_cost_usd),
        "output_cost_usd": _json_cost(record.output_cost_usd),
        "input_cost_credits": _json_cost(record.input_cost_credits),
        "cached_read_cost_credits": _json_cost(record.cached_input_cost_credits),
        "cached_write_cost_credits": _json_cost(record.cache_write_cost_credits),
        "output_cost_credits": _json_cost(record.output_cost_credits),
    }


def _json_output(records: list[ModelPricing], unit: str) -> None:
    payload = {
        "version": __version__,
        "unit": unit,
        "count": len(records),
        "models": [_record_payload(record) for record in records],
    }
    _print_json(payload)


def _print_json(payload: dict[str, object]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    try:
        print(text)
    except UnicodeEncodeError:
        print(json.dumps(payload, ensure_ascii=True, indent=2))


def _update_payload(info: UpdateInfo | None) -> dict[str, object]:
    return {
        "current": __version__,
        "latest": info.version_label if info else None,
        "update_available": bool(info and info.is_newer),
        "tag": info.tag if info else None,
        "url": info.html_url if info else None,
        "assets": [asset.name for asset in info.assets] if info else [],
    }


def _safe_check_updates(console: Console, *, quiet: bool = True) -> UpdateInfo | None:
    try:
        return check_for_update()
    except UpdateError as error:
        if not quiet:
            console.print(f"Controllo aggiornamenti non riuscito: {error}", style="yellow")
        return None


def _restart_arguments(args: argparse.Namespace) -> list[str]:
    """Preserve profile and view options across an automatic restart.

    Explicit one-shot actions such as ``--setup``, ``--export`` and
    ``--update`` are intentionally omitted so an update cannot repeat them.
    """

    result: list[str] = []
    if getattr(args, "config_file", None):
        result.extend(("--config-file", str(Path(args.config_file).expanduser().resolve())))
    if getattr(args, "selection_file", None):
        result.extend(("--selection-file", str(Path(args.selection_file).expanduser().resolve())))
    if getattr(args, "all_models", False):
        result.append("--all-models")
    if getattr(args, "no_export", False):
        result.append("--no-export")
    if getattr(args, "sort", None):
        result.extend(("--sort", args.sort))
    if getattr(args, "unit", None):
        result.extend(("--unit", args.unit))
    if getattr(args, "limit", None):
        result.extend(("--limit", str(args.limit)))
    for option, values in (
        ("--model", getattr(args, "model", [])),
        ("--search", getattr(args, "search", [])),
        ("--provider", getattr(args, "provider", [])),
        ("--category", getattr(args, "category", [])),
        ("--tier", getattr(args, "tier", [])),
        ("--status", getattr(args, "status", [])),
        ("--threshold", getattr(args, "threshold", [])),
    ):
        for value in values:
            result.extend((option, value))
    return result


def _report_update_check(console: Console, as_json: bool) -> int:
    try:
        info = check_for_update()
    except UpdateError as error:
        if as_json:
            _print_json({"error": str(error)})
        else:
            console.print(f"Controllo aggiornamenti non riuscito: {error}", style="red")
        return 1

    if as_json:
        _print_json(_update_payload(info))
    elif info is None:
        console.print("Il repository pubblico non contiene ancora release.", style="yellow")
    elif info.is_newer:
        console.print(
            f"Aggiornamento disponibile: {info.version_label} (attuale {__version__}).",
            style="bold yellow",
        )
        console.print(f"Note: {info.html_url}", style="dim")
    else:
        console.print(f"ModelsCompare {__version__} è aggiornato.", style="green")
    return 0


def _run_update_command(console: Console, as_json: bool, restart_args: list[str] | None = None) -> int:
    try:
        info = check_for_update()
    except UpdateError as error:
        if as_json:
            _print_json({"error": str(error)})
        else:
            console.print(f"Controllo aggiornamenti non riuscito: {error}", style="red")
        return 1

    if info is None or not info.is_newer:
        if as_json:
            _print_json(_update_payload(info))
        else:
            console.print(f"ModelsCompare {__version__} è aggiornato.", style="green")
        return 0

    try:
        result = install_update(info, restart_args=restart_args or ())
    except UpdateError as error:
        if as_json:
            _print_json({"error": str(error)})
        else:
            console.print(f"Aggiornamento non riuscito: {error}", style="red")
        return 1

    payload = _update_payload(info)
    payload.update({"scheduled": result.scheduled, "message": result.message})
    if as_json:
        _print_json(payload)
    else:
        console.print(result.message, style="green" if result.scheduled else "yellow")
    return 0 if result.scheduled else 2


def _auto_update(
    info: UpdateInfo,
    preferences: Preferences,
    args: argparse.Namespace,
    console: Console,
) -> bool:
    """Install an update without prompting, but only for standalone builds."""

    if (
        not info.is_newer
        or args.no_auto_update
        or args.setup
        or args.export
        or args.clear_selection
        or not preferences.auto_update
        or not is_frozen()
    ):
        return False
    try:
        result = install_update(info, restart_args=_restart_arguments(args))
    except UpdateError as error:
        console.print(f"Aggiornamento automatico non riuscito: {error}", style="yellow")
        return False
    if result.scheduled:
        console.print(result.message, style="green")
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    console = Console()

    if args.check_updates:
        return _report_update_check(console, args.json)
    if args.update:
        return _run_update_command(console, args.json, _restart_arguments(args))

    config_path = args.config_file or default_preferences_path()
    legacy_path = args.selection_file or config_path.with_name("selection.json")
    try:
        preferences = load_preferences(config_path, legacy_path)
    except (OSError, ValueError) as error:
        console.print(f"Configurazione non valida: {error}. Correggi il file o avvia con --setup.", style="red")
        return 2

    interactive = console.is_terminal and sys.stdin.isatty()
    update_info: UpdateInfo | None = None
    if interactive and not args.json and not args.no_update_check and preferences.check_updates:
        update_info = _safe_check_updates(console)
        if update_info is not None and _auto_update(update_info, preferences, args, console):
            return 0

    try:
        records = fetch_live_records()
    except (requests.RequestException, ValueError) as error:
        console.print(f"Impossibile leggere i prezzi live: {error}", style="red")
        if update_info is not None and update_info.is_newer:
            console.print(
                f"È disponibile un aggiornamento: {update_info.version_label} "
                f"(attuale {__version__}). Esegui `modelscompare --update`.",
                style="yellow",
            )
        return 1

    if args.clear_selection:
        preferences.favorite_models = []
        preferences.setup_completed = True
        save_preferences(preferences, config_path)

    if not preferences.setup_completed or args.setup:
        if not interactive:
            console.print("Il setup richiede un terminale interattivo. Avvia modelscompare in un terminale.", style="yellow")
            return 2
        configured = run_setup_wizard(records, preferences)
        if configured is None:
            console.print("Setup annullato; la configurazione esistente non è stata modificata.", style="yellow")
            return 0
        preferences = configured
        save_preferences(preferences, config_path)
        console.print(f"Configurazione salvata in {config_path}", style="green")

    preferences.favorite_models = list(dict.fromkeys(preferences.favorite_models))
    favorites = {name.casefold() for name in preferences.favorite_models}
    current_preferences = preferences
    active_unit = args.unit or preferences.display_unit
    filtered = filter_records(records, preferences, args, include_saved_filters=False)
    search_text = ""

    if not interactive:
        if args.json:
            _json_output(filtered, active_unit)
        else:
            _plain_output(filtered, active_unit)
        if args.export:
            if _export_with_consent(
                filtered,
                preferences,
                args.output_prefix,
                console,
                confirmed=True,
                output_dir=args.output_dir,
            ) is None:
                return 1
        return 0

    if args.export:
        if _export_with_consent(
            filtered,
            current_preferences,
            args.output_prefix,
            console,
            confirmed=True,
            output_dir=args.output_dir,
        ) is None:
            return 1

    def _set_search(value: str) -> None:
        nonlocal search_text
        search_text = value

    def _set_unit(value: str) -> None:
        nonlocal active_unit
        active_unit = value
        _persist_unit(value, current_preferences, config_path)

    while True:
        browser = ModelBrowser(
            filtered,
            favorites=favorites,
            display_unit=active_unit,
            search_text=search_text,
            update_info=update_info,
            favorites_only=bool(favorites) and not args.all_models,
            on_favorites_changed=lambda names: _persist_favorites(names, records, current_preferences, config_path),
            on_unit_changed=_set_unit,
            on_search_changed=_set_search,
            open_url=webbrowser.open,
        )
        action = browser.run()
        if action == "quit":
            return 0
        if action == "search":
            try:
                query = Prompt.ask(
                    "Cerca (nome, provider, categoria, stato; vuoto per pulire)",
                    default=search_text,
                    console=console,
                )
            except (EOFError, KeyboardInterrupt):
                continue
            search_text = query.strip()
            continue
        if action == "update":
            try:
                fresh_info = check_for_update()
            except UpdateError as error:
                console.print(f"Controllo aggiornamenti non riuscito: {error}", style="red")
                continue
            update_info = fresh_info
            if update_info is not None and update_info.is_newer:
                console.print(
                    f"Aggiornamento disponibile: {update_info.version_label} "
                    f"(attuale {__version__}).",
                    style="bold yellow",
                )
                if Confirm.ask("Scaricare e installare ora?", default=True, console=console):
                    try:
                        result = install_update(update_info, restart_args=_restart_arguments(args))
                    except UpdateError as error:
                        console.print(f"Aggiornamento non riuscito: {error}", style="red")
                    else:
                        console.print(result.message, style="green" if result.scheduled else "yellow")
            else:
                console.print(f"ModelsCompare {__version__} è aggiornato.", style="green")
            continue
        if action == "setup":
            configured = run_setup_wizard(records, current_preferences)
            if configured is not None:
                current_preferences = configured
                preferences = configured
                save_preferences(preferences, config_path)
                favorites = {name.casefold() for name in preferences.favorite_models}
                active_unit = args.unit or preferences.display_unit
                filtered = filter_records(records, preferences, args, include_saved_filters=False)
            continue
        if action == "export":
            if args.no_export:
                console.print("Export disabilitato da --no-export.", style="yellow")
            else:
                _export_with_consent(
                    filtered,
                    current_preferences,
                    args.output_prefix,
                    console,
                    output_dir=args.output_dir,
                )
            continue
        if action == "refresh":
            try:
                records = fetch_live_records()
            except (requests.RequestException, ValueError) as error:
                console.print(f"Aggiornamento non riuscito: {error}", style="red")
                continue
            filtered = filter_records(records, current_preferences, args, include_saved_filters=False)
            continue


def _persist_favorites(
    favorite_keys: set[str],
    records: list[ModelPricing],
    preferences: Preferences,
    config_path: Path,
) -> None:
    known_keys = {record.name.casefold() for record in records}
    selected = [
        record.name for record in records if record.name.casefold() in favorite_keys
    ]
    preserved = [
        name for name in preferences.favorite_models
        if name.casefold() not in known_keys
    ]
    preferences.favorite_models = list(dict.fromkeys(selected + preserved))
    save_preferences(preferences, config_path)


def _persist_unit(unit: str, preferences: Preferences, config_path: Path) -> None:
    preferences.display_unit = unit
    save_preferences(preferences, config_path)


if __name__ == "__main__":
    raise SystemExit(main())
