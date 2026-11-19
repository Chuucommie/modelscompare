import webbrowser
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any

from prompt_toolkit import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth

from modelscompare.models import ModelPricing


def _terminal_size() -> tuple[int, int]:
    try:
        size = get_app().output.get_size()
        return max(40, size.columns), max(10, size.rows)
    except Exception:
        return 100, 30


def _clip(text: str, width: int) -> str:
    """Truncate text by terminal display width, not by code points."""

    if width <= 0:
        return ""
    if get_cwidth(text) <= width:
        return text
    if width <= 3:
        return "." * width
    result: list[str] = []
    used = 0
    for character in text:
        character_width = get_cwidth(character)
        if used + character_width > width - 3:
            break
        result.append(character)
        used += character_width
    return "".join(result) + "..."[: max(0, width - used)]


def _pad(text: str, width: int, align: str = "left") -> str:
    text = _clip(text, width)
    gap = max(0, width - get_cwidth(text))
    if align == "right":
        return " " * gap + text
    if align == "center":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap


def _fit_segments(
    segments: Sequence[tuple[str, str]],
    width: int,
    pad_style: str,
) -> list[tuple[str, str]]:
    fragments: list[tuple[str, str]] = []
    used = 0
    for style, text in segments:
        if used >= width:
            break
        clipped = _clip(text, width - used)
        if clipped:
            fragments.append((style, clipped))
            used += get_cwidth(clipped)
    if used < width:
        fragments.append((pad_style, " " * (width - used)))
    return fragments


def _search_blob(record: ModelPricing) -> str:
    return " ".join(
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


def _footer_lines(items: Sequence[tuple[str, str]], width: int) -> list[list[tuple[str, str]]]:
    lines: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    used = 0
    for cap, description in items:
        cap_text = f" [{cap}] "
        desc_text = f"{description}  "
        item_width = get_cwidth(cap_text) + get_cwidth(desc_text)
        if current and used + item_width > width:
            lines.append(current)
            current = []
            used = 0
        if item_width > width:
            desc_text = _clip(desc_text, max(0, width - get_cwidth(cap_text)))
            item_width = get_cwidth(cap_text) + get_cwidth(desc_text)
        current.extend(
            (
                ("class:keys.cap", cap_text),
                ("class:keys.desc", desc_text),
            )
        )
        used += item_width
    if current:
        lines.append(current)
    return lines


class ModelBrowser:
    def __init__(
        self,
        records: Sequence[ModelPricing],
        favorites: set[str],
        display_unit: str = "usd",
        on_favorites_changed: Callable[[set[str]], None] | None = None,
        on_unit_changed: Callable[[str], None] | None = None,
        on_search_changed: Callable[[str], None] | None = None,
        open_url: Callable[[str], Any] = webbrowser.open,
        input: Any = None,
        output: Any = None,
        search_text: str = "",
        update_info: Any = None,
        favorites_only: bool = False,
    ) -> None:
        self.records = list(records)
        self.favorites = {name.casefold() for name in favorites}
        self.display_unit = display_unit
        self.on_favorites_changed = on_favorites_changed or (lambda _favorites: None)
        self.on_unit_changed = on_unit_changed or (lambda _unit: None)
        self.on_search_changed = on_search_changed or (lambda _search: None)
        self.open_url = open_url
        self.input = input
        self.output = output
        self.index = 0
        self.favorites_only = favorites_only
        self.comparison_selection: set[str] = set()
        self.comparison_keys: set[str] = set()
        self.comparing = False
        self.show_help = False
        self.search_text = search_text
        self.update_info = update_info
        self.result: str | None = None

    @property
    def visible_records(self) -> list[ModelPricing]:
        records = self.records
        if self.search_text.strip():
            query = self.search_text.strip().casefold()
            records = [record for record in records if query in _search_blob(record)]
        if self.comparing:
            return [
                record for record in records
                if self._record_key(record) in self.comparison_keys
            ]
        if not self.favorites_only:
            return records
        return [record for record in records if record.name.casefold() in self.favorites]

    def _record_key(self, record: ModelPricing) -> str:
        return "|".join(
            (
                record.name.casefold(),
                record.provider.casefold(),
                record.tier.casefold(),
                record.threshold.casefold(),
            )
        )

    @property
    def selected_record(self) -> ModelPricing | None:
        visible = self.visible_records
        if not visible:
            return None
        self.index = min(max(0, self.index), len(visible) - 1)
        return visible[self.index]

    def _format_cost(self, record: ModelPricing, field: str) -> str:
        attribute = f"{field}_cost_credits" if field != "cached_read" else "cached_input_cost_credits"
        usd_attribute = f"{field}_cost_usd" if field != "cached_read" else "cached_input_cost_usd"
        value = getattr(record, usd_attribute if self.display_unit == "usd" else attribute)
        if value is None:
            return "N/A"
        if self.display_unit == "usd":
            if abs(value) >= Decimal("0.0001"):
                decimals = 4
            elif abs(value) >= Decimal("0.000001"):
                decimals = 6
            else:
                decimals = 8
            return f"${value:,.{decimals}f}"
        if abs(value) >= Decimal("0.01"):
            decimals = 2
        elif abs(value) >= Decimal("0.0001"):
            decimals = 4
        else:
            decimals = 6
        return f"{value:,.{decimals}f} cr"

    def _header(self) -> FormattedText:
        width, _height = _terminal_size()
        box_width = min(width, 122)
        content_width = max(20, box_width - 5)
        visible = self.visible_records
        count = len(visible)
        selected_count = len(self.comparison_keys)
        unit = "USD / 1M token" if self.display_unit == "usd" else "crediti AI / 1M token"
        position = f"{min(self.index + 1, count)}/{count}" if count else "0/0"

        fragments: list[tuple[str, str]] = [
            ("class:banner.frame", " ╭" + "─" * (box_width - 3) + "╮\n"),
            ("class:banner.frame", " │ "),
        ]

        first_line: list[tuple[str, str]] = [
            ("class:banner.brand", "⚡ MODELS"),
            ("class:banner.brand", " "),
            ("class:banner.logoaccent", "COMPARE"),
            ("class:banner.title", "  •  GitHub Copilot Live Pricing"),
        ]
        compact_header = content_width < 60
        if self.search_text.strip():
            first_line.append(("class:badge.search", f"  🔎 {self.search_text.strip()} "))
        elif compact_header:
            first_line.append(("class:banner.title", f"  {count} modelli  {position}"))
        else:
            first_line.append(("class:banner.title", f"  •  {count} modelli"))
        fragments.extend(_fit_segments(first_line, content_width, "class:banner.title"))
        fragments.append(("class:banner.frame", " │\n"))

        second_line: list[tuple[str, str]] = [("class:badge.live", " ● LIVE ")]
        if self.update_info is not None and getattr(self.update_info, "is_newer", False):
            second_line.append(
                ("class:badge.update", f" ↑ {self.update_info.version_label} ")
            )
        if self.comparing:
            second_line.append(("class:badge.compare", f" confronto ({selected_count}) "))
        elif self.favorites_only:
            second_line.append(("class:badge.mode", f" PREFERITI ({count}) "))
        else:
            second_line.append(("class:badge.mode", " TUTTI I MODELLI "))
        second_line.extend(
            (
                ("class:badge.unit", f" {unit} "),
            )
        )
        if not compact_header:
            second_line.append(("class:banner.title", f"  {position} "))
        fragments.append(("class:banner.frame", " │ "))
        fragments.extend(_fit_segments(second_line, content_width, "class:banner.title"))
        fragments.append(("class:banner.frame", " │\n"))
        fragments.append(("class:banner.frame", " ╰" + "─" * (box_width - 3) + "╯\n"))
        return FormattedText(fragments)

    def _layout(self, width: int) -> dict[str, Any]:
        if width >= 112:
            return {
                "model": max(18, min(32, width - 91)),
                "ctx": 14,
                "type": 12,
                "cost": 11,
                "cached": 11,
                "show_cached": True,
                "show_ctx": True,
                "type_short": False,
            }
        if width >= 100:
            return {
                "model": max(16, min(30, width - 84)),
                "ctx": 12,
                "type": 9,
                "cost": 11,
                "cached": 10,
                "show_cached": True,
                "show_ctx": True,
                "type_short": True,
            }
        if width >= 78:
            return {
                "model": max(14, min(40, width - 58)),
                "ctx": 10,
                "type": 9,
                "cost": 11,
                "cached": 0,
                "show_cached": False,
                "show_ctx": True,
                "type_short": True,
            }
        return {
            "model": max(2, min(48, width - 38)),
            "ctx": 0,
            "type": 1,
            "cost": 11,
            "cached": 0,
            "show_cached": False,
            "show_ctx": False,
            "type_short": True,
        }

    def _type_label(self, record: ModelPricing, short: bool, tiny: bool = False) -> str:
        if tiny:
            if not record.reasoning_known:
                return "?"
            return "R" if record.is_reasoning else "S"
        if not record.reasoning_known:
            return "N/D"
        if short:
            return "Reasoning" if record.is_reasoning else "Standard"
        return "🧠 Reasoning" if record.is_reasoning else "Standard"

    def _row_fragments(self) -> FormattedText:
        if self.show_help:
            return self._help_fragments()

        visible = self.visible_records
        width, height = _terminal_size()
        if not visible:
            if self.search_text.strip():
                message = "\n Nessun modello corrisponde alla ricerca. Premi Esc per pulire la ricerca.\n"
            elif self.comparing:
                message = "\n Nessun modello selezionato per il confronto. Premi Esc per tornare all'elenco completo.\n"
            else:
                if self.records and self.favorites_only:
                    message = "\n Nessun preferito salvato. Premi A per tornare all'elenco di tutti i modelli.\n"
                else:
                    message = "\n Nessun modello corrisponde ai filtri. Modifica i filtri o premi S per il setup.\n"
            return FormattedText([("class:muted", message)])

        layout = self._layout(width)
        model_w = layout["model"]
        ctx_w = layout["ctx"]
        type_w = layout["type"]
        cost_w = layout["cost"]
        cached_w = layout["cached"]

        fragments: list[tuple[str, str]] = []
        header = "  › ★ ✓  " + _pad("MODELLO", model_w)
        if layout["show_ctx"]:
            header += "  " + _pad("CONTESTO", ctx_w)
        header += "  " + _pad("T" if type_w <= 1 else "TIPO", type_w, "center" if type_w <= 2 else "left")
        header += "  " + _pad("INPUT", cost_w, "right")
        if layout["show_cached"]:
            header += "  " + _pad("CACHED-R", cached_w, "right")
            header += "  " + _pad("CACHED-W", cached_w, "right")
        header += "  " + _pad("OUTPUT", cost_w, "right") + "\n"
        total_width = get_cwidth(header.rstrip("\n"))
        fragments.append(("class:table.header", header))
        fragments.append(("class:table.divider", "  " + "─" * max(0, total_width - 2) + "\n"))

        display_height = max(1, height - 13)
        start = max(0, min(self.index - display_height // 2, len(visible) - display_height))
        stop = min(len(visible), start + display_height)

        for row_index in range(start, stop):
            record = visible[row_index]
            selected = row_index == self.index
            favorite = record.name.casefold() in self.favorites
            in_compare = self._record_key(record) in self.comparison_keys

            cursor = "›" if selected else " "
            star = "★" if favorite else "·"
            check = "✓" if in_compare else "·"
            name = _pad(record.name, model_w)
            type_label = self._type_label(record, layout["type_short"], type_w <= 1)
            input_val = self._format_cost(record, "input")
            cached_r_val = self._format_cost(record, "cached_read")
            cached_w_val = self._format_cost(record, "cache_write")
            output_val = self._format_cost(record, "output")

            if selected:
                row = f"  {cursor} {star} {check}  {name}"
                if layout["show_ctx"]:
                    row += "  " + _pad(record.context_label, ctx_w)
                row += "  " + _pad(type_label, type_w, "center" if type_w <= 2 else "left")
                row += "  " + _pad(input_val, cost_w, "right")
                if layout["show_cached"]:
                    row += "  " + _pad(cached_r_val, cached_w, "right")
                    row += "  " + _pad(cached_w_val, cached_w, "right")
                row += "  " + _pad(output_val, cost_w, "right") + "\n"
                fragments.append(("class:selected", row))
                continue

            fragments.extend(
                (
                    ("class:row.cursor", f"  {cursor} "),
                    ("class:row.star_active" if favorite else "class:row.star_inactive", f"{star} "),
                    ("class:row.check_active" if in_compare else "class:row.check_inactive", f"{check}  "),
                    ("class:row.name", f"{name}  "),
                )
            )
            if layout["show_ctx"]:
                context_text = _pad(record.context_label, ctx_w)
                context_style = (
                    "class:row.context_long"
                    if "long" in context_text.casefold()
                    else "class:row.context_def"
                )
                fragments.append((context_style, f"{context_text}  "))
            if not record.reasoning_known:
                type_style = "class:row.cost_na"
            else:
                type_style = "class:row.reasoning_on" if record.is_reasoning else "class:row.reasoning_off"
            fragments.append((type_style, f"{_pad(type_label, type_w, 'center' if type_w <= 2 else 'left')}  "))
            fragments.append(("class:row.cost_input", f"{_pad(input_val, cost_w, 'right')}  "))
            if layout["show_cached"]:
                fragments.append(("class:row.cost_cached", f"{_pad(cached_r_val, cached_w, 'right')}  "))
                fragments.append(
                    (
                        "class:row.cost_na" if cached_w_val == "N/A" else "class:row.cost_write",
                        f"{_pad(cached_w_val, cached_w, 'right')}  ",
                    )
                )
            fragments.append(("class:row.cost_output", f"{_pad(output_val, cost_w, 'right')}\n"))

        return FormattedText(fragments)

    def _help_fragments(self) -> FormattedText:
        width, _height = _terminal_size()
        box_width = min(width, 122)
        content_width = max(20, box_width - 5)
        lines = [
            ("class:help.key", "↑/↓ Home/End PgUp/PgDn"),
            ("class:help.text", "Naviga nell'elenco"),
            ("class:help.key", "Space"),
            ("class:help.text", "Aggiungi/rimuovi preferito (★)"),
            ("class:help.key", "M"),
            ("class:help.text", "Seleziona il modello per il confronto (✓)"),
            ("class:help.key", "C"),
            ("class:help.text", "Mostra il confronto selezionato / torna alla lista"),
            ("class:help.key", "A"),
            ("class:help.text", "Alterna tutti i modelli e i preferiti"),
            ("class:help.key", "T"),
            ("class:help.text", "Cambia unità di visualizzazione USD / crediti AI"),
            ("class:help.key", "/"),
            ("class:help.text", "Cerca per nome, provider, categoria o stato"),
            ("class:help.key", "Enter"),
            ("class:help.text", "Apri la scheda Artificial Analysis del modello"),
            ("class:help.key", "S E R"),
            ("class:help.text", "Setup • Esporta • Ricarica i prezzi live"),
            ("class:help.key", "U"),
            ("class:help.text", "Cerca aggiornamenti nelle release GitHub"),
            ("class:help.key", "H / ?"),
            ("class:help.text", "Chiudi questo aiuto"),
            ("class:help.key", "Q / Ctrl+C"),
            ("class:help.text", "Esci"),
        ]
        fragments: list[tuple[str, str]] = [
            ("class:card.frame", " ╭─"), ("class:card.title", " Aiuto "),
            ("class:card.frame", "─" * max(0, box_width - 11) + "╮\n"),
        ]
        pairs = list(zip(lines[1::2], lines[2::2]))
        for key, description in pairs:
            fragments.append(("class:card.frame", " │ "))
            key_text = _pad(key[1], 22)
            fragments.append((key[0], key_text))
            fragments.append((description[0], _pad(description[1], content_width - 22)))
            fragments.append(("class:card.frame", " │\n"))
        fragments.append(("class:card.frame", " ╰" + "─" * (box_width - 3) + "╯"))
        return FormattedText(fragments)

    def _detail_card(self) -> FormattedText:
        record = self.selected_record
        width, _height = _terminal_size()
        box_width = min(width, 122)
        content_width = max(20, box_width - 5)

        if record is None:
            return FormattedText([
                ("class:card.frame", " ╭─ Dettagli " + "─" * max(0, box_width - 14) + "╮\n"),
                ("class:card.frame", " │ "), ("class:muted", _pad("Nessun modello disponibile", content_width)),
                ("class:card.frame", " │\n"),
                ("class:card.frame", " │ "), ("class:muted", " " * content_width), ("class:card.frame", " │\n"),
                ("class:card.frame", " ╰" + "─" * (box_width - 3) + "╯"),
            ])

        title = _clip(f" Dettagli Modello: {record.name} ", content_width)
        line1_items = [
            f"Provider: {record.provider or 'N/D'}",
            f"Tier: {record.tier or 'Default'}",
        ]
        if record.category:
            line1_items.append(f"Categoria: {record.category}")
        line1_items.append(f"Soglia: {record.threshold or 'N/D'}")
        line1 = "   •   ".join(line1_items)

        threshold = record.threshold if record.threshold.casefold() not in {"", "not applicable"} else ""
        context_window = record.context_window or threshold or "N/D"
        link_state = "disponibile" if record.artificial_analysis_url else "N/D"
        line2 = (
            f"Tipo: {record.reasoning_label}   •   Finestra: {context_window}"
            f"   •   Scheda AA: {link_state}"
        )

        return FormattedText([
            ("class:card.frame", " ╭─"), ("class:card.title", title),
            ("class:card.frame", "─" * max(0, box_width - 4 - get_cwidth(title)) + "╮\n"),
            ("class:card.frame", " │ "), ("class:card.value", _pad(line1, content_width)),
            ("class:card.frame", " │\n"),
            ("class:card.frame", " │ "), ("class:card.value", _pad(line2, content_width)),
            ("class:card.frame", " │\n"),
            ("class:card.frame", " ╰" + "─" * (box_width - 3) + "╯"),
        ])

    def _footer(self) -> FormattedText:
        width, _height = _terminal_size()
        if self.show_help:
            return FormattedText([
                ("class:keys.cap", " [H] "), ("class:keys.desc", "Chiudi aiuto  "),
                ("class:keys.cap", " [Q] "), ("class:keys.desc", "Esci"),
            ])

        sel_count = len(self.comparison_keys)
        compare_label = "Lista" if self.comparing else f"Confronta ({sel_count})"
        if width < 50:
            items: list[tuple[str, str]] = [
                ("↑/↓", "Naviga"),
                ("Space", "Preferito"),
                ("M", "Seleziona"),
                ("C", compare_label),
                ("H", "Aiuto"),
                ("Q", "Esci"),
            ]
        elif width < 80:
            items = [
                ("↑/↓", "Naviga"),
                ("Space", "Preferito"),
                ("M", "Seleziona"),
                ("C", compare_label),
                ("/", "Cerca"),
                ("T", f"Unità {self.display_unit.upper()}"),
                ("H", "Aiuto"),
                ("Q", "Esci"),
            ]
        else:
            items = [
                ("↑/↓", "Naviga"),
                ("Space", "Preferito"),
                ("M", "Seleziona"),
                ("C", compare_label),
                ("/", "Cerca"),
                ("T", f"Unità {self.display_unit.upper()}"),
                ("H", "Aiuto"),
            ]
            if self.search_text.strip() or self.comparing:
                items.append(("Esc", "Pulisci/Vista"))
            items.extend(
                (
                    ("Enter", "Scheda AA"),
                    ("A", "Tutti/Pref"),
                    ("S", "Setup"),
                    ("E", "Export"),
                    ("R", "Ricarica"),
                )
            )
            if self.update_info is not None and getattr(self.update_info, "is_newer", False):
                items.append(("U", f"Aggiorna {self.update_info.version_label}"))
            else:
                items.append(("U", "Aggiornamenti"))
            items.append(("Q", "Esci"))

        fragments: list[tuple[str, str]] = []
        for line in _footer_lines(items, width):
            fragments.extend(line)
            fragments.append(("", "\n"))
        return FormattedText(fragments)

    def create_application(self) -> Application[str]:
        bindings = KeyBindings()

        def move(amount: int) -> None:
            count = len(self.visible_records)
            if count:
                self.index = min(max(self.index + amount, 0), count - 1)

        @bindings.add("up")
        def _up(event) -> None:
            if self.show_help:
                return
            move(-1)
            event.app.invalidate()

        @bindings.add("down")
        def _down(event) -> None:
            if self.show_help:
                return
            move(1)
            event.app.invalidate()

        @bindings.add("home")
        def _home(event) -> None:
            if self.show_help:
                return
            self.index = 0
            event.app.invalidate()

        @bindings.add("end")
        def _end(event) -> None:
            if self.show_help:
                return
            self.index = max(0, len(self.visible_records) - 1)
            event.app.invalidate()

        @bindings.add("pageup")
        def _page_up(event) -> None:
            if self.show_help:
                return
            move(-10)
            event.app.invalidate()

        @bindings.add("pagedown")
        def _page_down(event) -> None:
            if self.show_help:
                return
            move(10)
            event.app.invalidate()

        @bindings.add("enter")
        def _open_selected(event) -> None:
            if self.show_help:
                self.show_help = False
                event.app.invalidate()
                return
            record = self.selected_record
            if record and record.artificial_analysis_url:
                self.open_url(record.artificial_analysis_url)

        @bindings.add(" ")
        def _toggle_favorite(event) -> None:
            if self.show_help:
                return
            record = self.selected_record
            if record is None:
                return
            key = record.name.casefold()
            if key in self.favorites:
                self.favorites.remove(key)
            else:
                self.favorites.add(key)
            self.on_favorites_changed(set(self.favorites))
            if self.favorites_only and key not in self.favorites:
                self.index = min(self.index, max(0, len(self.visible_records) - 1))
            event.app.invalidate()

        @bindings.add("a")
        def _toggle_view(event) -> None:
            if self.show_help:
                return
            self.favorites_only = not self.favorites_only
            self.index = min(self.index, max(0, len(self.visible_records) - 1))
            event.app.invalidate()

        @bindings.add("m")
        def _toggle_comparison_selection(event) -> None:
            if self.show_help:
                return
            record = self.selected_record
            if record is None:
                return
            row_key = self._record_key(record)
            if row_key in self.comparison_keys:
                self.comparison_keys.remove(row_key)
            else:
                self.comparison_keys.add(row_key)
            self.comparison_selection = {
                candidate.name.casefold()
                for candidate in self.records
                if self._record_key(candidate) in self.comparison_keys
            }
            event.app.invalidate()

        @bindings.add("c")
        def _toggle_comparison_view(event) -> None:
            if self.show_help:
                return
            if self.comparing:
                self.comparing = False
                self.index = 0
            elif self.comparison_keys:
                self.comparing = True
                self.index = 0
            event.app.invalidate()

        @bindings.add("t")
        def _toggle_unit(event) -> None:
            if self.show_help:
                return
            self.display_unit = "credits" if self.display_unit == "usd" else "usd"
            self.on_unit_changed(self.display_unit)
            event.app.invalidate()

        @bindings.add("/")
        def _search(event) -> None:
            if not self.show_help:
                event.app.exit(result="search")

        @bindings.add("u")
        def _updates(event) -> None:
            if not self.show_help:
                event.app.exit(result="update")

        @bindings.add("h")
        @bindings.add("?")
        def _toggle_help(event) -> None:
            self.show_help = not self.show_help
            event.app.invalidate()

        @bindings.add("escape")
        def _return_to_list(event) -> None:
            if self.show_help:
                self.show_help = False
                event.app.invalidate()
                return
            if self.comparing:
                self.comparing = False
                self.index = 0
                event.app.invalidate()
                return
            if self.search_text:
                self.search_text = ""
                self.on_search_changed("")
                self.index = 0
                event.app.invalidate()

        @bindings.add("s")
        def _setup(event) -> None:
            if not self.show_help:
                event.app.exit(result="setup")

        @bindings.add("e")
        def _export(event) -> None:
            if not self.show_help:
                event.app.exit(result="export")

        @bindings.add("r")
        def _refresh(event) -> None:
            if not self.show_help:
                event.app.exit(result="refresh")

        @bindings.add("q")
        @bindings.add("c-c")
        def _quit(event) -> None:
            event.app.exit(result="quit")

        style = Style.from_dict({
            "banner.frame": "#2A454D",
            "banner.brand": "bold #5CE1E6",
            "banner.logoaccent": "bold #E0B76A",
            "banner.title": "#9AB2B8",
            "badge.live": "bold bg:#123528 #4ECCA3",
            "badge.unit": "bold bg:#332912 #F5CE69",
            "badge.mode": "bold bg:#162E3B #7AD2E6",
            "badge.compare": "bold bg:#3B1E32 #FF80AB",
            "badge.search": "bold bg:#1C3B2A #A5E887",
            "badge.update": "bold bg:#3B2E12 #FFD166",
            "keys.cap": "bold #5CE1E6",
            "keys.desc": "#8EA6AC",
            "table.header": "bold #7BE0AD",
            "table.divider": "#2A454D",
            "row.cursor": "bold #5CE1E6",
            "row.star_active": "bold #F9D423",
            "row.star_inactive": "#3A5056",
            "row.check_active": "bold #4ECCA3",
            "row.check_inactive": "#3A5056",
            "row.name": "#E3ECEB",
            "row.context_def": "#80CBC4",
            "row.context_long": "bold #FFB74D",
            "row.context_std": "#78909C",
            "row.reasoning_on": "bold #CE93D8",
            "row.reasoning_off": "#607D8B",
            "row.cost_input": "#B2DFDB",
            "row.cost_cached": "#80DEEA",
            "row.cost_write": "#FFE082",
            "row.cost_output": "#A7FFEB",
            "row.cost_na": "#4A6369",
            "selected": "bold bg:#163A42 #FFFFFF",
            "card.frame": "#2A454D",
            "card.title": "bold #7BE0AD",
            "card.label": "#7A959B",
            "card.value": "#E0EBEA",
            "help.key": "bold #5CE1E6",
            "help.text": "#D0DDDC",
            "muted": "#7A959B",
        })

        layout = Layout(HSplit([
            Window(FormattedTextControl(self._header), height=4, always_hide_cursor=True),
            Window(FormattedTextControl(self._row_fragments), wrap_lines=False, always_hide_cursor=True),
            Window(FormattedTextControl(self._detail_card), height=4, always_hide_cursor=True),
            Window(FormattedTextControl(self._footer), height=3, always_hide_cursor=True, wrap_lines=False),
        ]))

        return Application(
            layout=layout,
            key_bindings=bindings,
            full_screen=True,
            mouse_support=False,
            style=style,
            input=self.input,
            output=self.output,
        )

    def run(self) -> str:
        return self.create_application().run()


def choose_options(
    title: str,
    options: Sequence[str],
    selected: Sequence[str] = (),
    *,
    multiple: bool = True,
    input: Any = None,
    output: Any = None,
) -> list[str] | None:
    choices = list(dict.fromkeys(options))
    selected_keys = {value.casefold() for value in selected}
    if not choices:
        return [] if multiple else None
    index = next((i for i, value in enumerate(choices) if value.casefold() in selected_keys), 0)
    checked = {i for i, value in enumerate(choices) if value.casefold() in selected_keys}
    if not multiple:
        checked = {index}
    outcome: list[str] | None = None
    bindings = KeyBindings()

    def finish(event) -> None:
        nonlocal outcome
        outcome = [choices[i] for i in sorted(checked)] if multiple else [choices[index]]
        event.app.exit()

    @bindings.add("up")
    def _up(event) -> None:
        nonlocal index
        index = max(0, index - 1)
        if not multiple:
            checked.clear()
            checked.add(index)
        event.app.invalidate()

    @bindings.add("down")
    def _down(event) -> None:
        nonlocal index
        index = min(len(choices) - 1, index + 1)
        if not multiple:
            checked.clear()
            checked.add(index)
        event.app.invalidate()

    @bindings.add("home")
    def _home(event) -> None:
        nonlocal index
        index = 0
        if not multiple:
            checked.clear()
            checked.add(index)
        event.app.invalidate()

    @bindings.add("end")
    def _end(event) -> None:
        nonlocal index
        index = len(choices) - 1
        if not multiple:
            checked.clear()
            checked.add(index)
        event.app.invalidate()

    @bindings.add("pageup")
    def _page_up(event) -> None:
        nonlocal index
        index = max(0, index - 10)
        if not multiple:
            checked.clear()
            checked.add(index)
        event.app.invalidate()

    @bindings.add("pagedown")
    def _page_down(event) -> None:
        nonlocal index
        index = min(len(choices) - 1, index + 10)
        if not multiple:
            checked.clear()
            checked.add(index)
        event.app.invalidate()

    @bindings.add(" ")
    def _toggle(event) -> None:
        if not multiple:
            checked.clear()
            checked.add(index)
        elif index in checked:
            checked.remove(index)
        else:
            checked.add(index)
        event.app.invalidate()

    @bindings.add("a")
    def _toggle_all(event) -> None:
        if not multiple:
            return
        if len(checked) == len(choices):
            checked.clear()
        else:
            checked.update(range(len(choices)))
        event.app.invalidate()

    @bindings.add("enter")
    def _enter(event) -> None:
        finish(event)

    @bindings.add("escape")
    @bindings.add("c-c")
    def _cancel(event) -> None:
        event.app.exit()

    def render_choices() -> FormattedText:
        width, height = _terminal_size()
        box_width = min(width, 84)
        content_width = max(20, box_width - 5)
        max_visible = max(1, height - 9)
        start = max(0, min(index - max_visible // 2, len(choices) - max_visible))
        stop = min(len(choices), start + max_visible)
        help_text = (
            "Frecce sposta • Spazio seleziona • A tutti • Invio conferma • Esc annulla"
            if multiple
            else "Frecce sposta • Invio conferma • Esc annulla"
        )

        fragments: list[tuple[str, str]] = [
            ("class:box", " ╭" + "─" * (box_width - 3) + "╮\n"),
            ("class:box", " │ "),
        ]
        fragments.extend(_fit_segments([("class:title", f" {title}")], content_width, "class:box"))
        fragments.append(("class:box", " │\n"))
        fragments.append(("class:box", " │ "))
        fragments.extend(_fit_segments([("class:help", f" {help_text}")], content_width, "class:box"))
        fragments.append(("class:box", " │\n"))
        fragments.append(("class:box", " │ "))
        position = f" {index + 1}/{len(choices)} • {len(checked)} selezionati"
        fragments.extend(_fit_segments([("class:help", position)], content_width, "class:box"))
        fragments.append(("class:box", " │\n"))
        fragments.append(("class:box", " ╰" + "─" * (box_width - 3) + "╯\n\n"))

        for choice_index in range(start, stop):
            value = choices[choice_index]
            marker = "›" if choice_index == index else " "
            checkmark = "[✓]" if choice_index in checked else "[ ]"
            line = _pad(f"  {marker} {checkmark} {value}", content_width)
            if choice_index == index:
                fragments.append(("class:selected", line + "\n"))
            else:
                fragments.append(("class:row", line + "\n"))
        if stop < len(choices):
            fragments.append(("class:help", "  … altre opzioni sotto; usa ↓ o PageDown\n"))
        return FormattedText(fragments)

    style = Style.from_dict({
        "box": "#2A454D",
        "title": "bold #5CE1E6",
        "help": "#8FA3A8",
        "row": "#D0DDDC",
        "selected": "bold bg:#163A42 #FFFFFF",
    })
    app = Application(
        layout=Layout(Window(FormattedTextControl(render_choices), always_hide_cursor=True, wrap_lines=False)),
        key_bindings=bindings,
        full_screen=True,
        mouse_support=False,
        style=style,
        input=input,
        output=output,
    )
    app.run()
    return outcome
