import csv
import io
import math
import os
import re
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from modelscompare.models import ModelPricing
from modelscompare.paths import default_downloads_dir

COST_COLUMNS = (
    ("Input", "input_cost_usd", "input_cost_credits"),
    ("Cached read", "cached_input_cost_usd", "cached_input_cost_credits"),
    ("Cached write", "cache_write_cost_usd", "cache_write_cost_credits"),
    ("Output", "output_cost_usd", "output_cost_credits"),
)

_ILLEGAL_XML_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_MAX_CELL_CHARS = 32767


def _write_unique_file(destination: Path, prefix: str, extension: str, content: bytes) -> Path:
    index = 0
    while True:
        suffix = f"_{index}" if index else ""
        path = destination / f"{prefix}{suffix}{extension}"
        try:
            file = path.open("xb")
        except FileExistsError:
            index += 1
            continue
        except PermissionError:
            if not path.exists():
                raise
            index += 1
            continue
        else:
            try:
                with file:
                    file.write(content)
                    file.flush()
                    os.fsync(file.fileno())
                return path
            except PermissionError:
                _remove_partial(path)
                index += 1
            except OSError:
                _remove_partial(path)
                raise


def _remove_partial(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _sanitize_text(value: str) -> str:
    """Remove characters Excel cannot store and enforce the cell limit."""

    cleaned = value.encode("utf-8", "replace").decode("utf-8")
    return _ILLEGAL_XML_CHARS.sub("", cleaned)[:_MAX_CELL_CHARS]


def _export_columns():
    columns = [
        ("Modello", "name", "text"),
        ("Provider", "provider", "text"),
        ("Categoria", "category", "text"),
        ("Stato di rilascio", "release_status", "text"),
        ("Tier", "tier", "text"),
        ("Soglia contesto", "threshold", "text"),
        ("Ragionamento", "reasoning_label", "text"),
        ("Finestra contesto", "context_window", "text"),
    ]
    for label, usd_field, credits_field in COST_COLUMNS:
        columns.extend((
            (f"{label} (USD / 1M token)", usd_field, "currency"),
            (f"{label} (crediti AI / 1M token)", credits_field, "credits"),
        ))
    columns.append(("Scheda Artificial Analysis", "artificial_analysis_url", "link"))
    return columns


def _excel_value(record: ModelPricing, attribute: str, kind: str):
    if kind == "link":
        return "Apri scheda" if record.artificial_analysis_url else ""
    value = getattr(record, attribute)
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        try:
            converted = float(value)
        except (OverflowError, ValueError):
            return None
        return converted if math.isfinite(converted) else None
    if isinstance(value, str):
        return _sanitize_text(value)
    return value


def _csv_value(record: ModelPricing, attribute: str):
    value = getattr(record, attribute)
    if isinstance(value, Decimal):
        return format(value, "f") if value.is_finite() else ""
    if isinstance(value, str):
        return _sanitize_text(value)
    return value


def _cost_decimals(value: float, *, credits: bool) -> int:
    magnitude = abs(value)
    if credits:
        if magnitude >= 0.01:
            return 2
        if magnitude >= 0.0001:
            return 4
        return 6
    if magnitude >= 0.0001:
        return 4
    if magnitude >= 0.000001:
        return 6
    return 8


def _validate_workbook_bytes(content: bytes) -> None:
    """Re-open the generated workbook so a corrupt file is never written."""

    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True)
    except Exception as error:  # openpyxl exposes several zip/XML errors
        raise ValueError(f"Generated Excel workbook is not readable: {error}") from error
    try:
        sheet = workbook.active
        if sheet.max_row < 1 or sheet.max_column < 1:
            raise ValueError("Generated Excel workbook is empty.")
    finally:
        workbook.close()


def export_records(
    records: list[ModelPricing],
    formats: list[str],
    prefix: str = "copilot_models",
    downloads_dir: Path | None = None,
) -> list[Path]:
    destination = downloads_dir or default_downloads_dir()
    destination.mkdir(parents=True, exist_ok=True)
    safe_prefix = Path(prefix).name or "copilot_models"
    if Path(safe_prefix).suffix.casefold() in {".csv", ".xlsx"}:
        safe_prefix = Path(safe_prefix).stem
    columns = _export_columns()
    headers = [header for header, _attribute, _kind in columns]
    output_paths: list[Path] = []

    if "csv" in formats:
        csv_buffer = io.StringIO(newline="")
        writer = csv.writer(csv_buffer)
        writer.writerow(headers)
        for record in records:
            writer.writerow([
                _csv_value(record, attribute)
                for _header, attribute, _kind in columns
            ])
        output_paths.append(_write_unique_file(
            destination, safe_prefix, ".csv", csv_buffer.getvalue().encode("utf-8-sig"),
        ))

    if "xlsx" in formats:
        workbook = Workbook()
        workbook.properties.creator = "ModelsCompare"
        workbook.properties.title = "Copilot model pricing"
        sheet = workbook.active
        sheet.title = "Copilot prices"
        sheet.sheet_properties.tabColor = "1D8792"
        sheet.sheet_view.showGridLines = False
        sheet.append(headers)
        for record in records:
            sheet.append([_excel_value(record, attribute, kind) for _header, attribute, kind in columns])

        header_fill = PatternFill("solid", fgColor="163B4D")
        header_font = Font(name="Aptos Display", size=10, bold=True, color="FFFFFF")
        header_border = Border(bottom=Side(style="medium", color="27A6A1"))
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)
            cell.border = header_border
        sheet.row_dimensions[1].height = 38

        for row_index, record in enumerate(records, start=2):
            row_fill = PatternFill("solid", fgColor="F2F7F8" if row_index % 2 == 0 else "FFFFFF")
            for column_index, (_header, _attribute, kind) in enumerate(columns, start=1):
                cell = sheet.cell(row=row_index, column=column_index)
                cell.fill = row_fill
                cell.alignment = Alignment(
                    vertical="center",
                    horizontal="right" if kind in {"currency", "credits"} else "left",
                )
                if kind == "currency" and cell.value is not None:
                    decimals = _cost_decimals(cell.value, credits=False)
                    pattern = "0" * decimals
                    cell.number_format = f"$#,##0.{pattern};[Red]($#,##0.{pattern});-"
                elif kind == "credits" and cell.value is not None:
                    decimals = _cost_decimals(cell.value, credits=True)
                    cell.number_format = f'#,##0.{"0" * decimals} "cr"'
            sheet.row_dimensions[row_index].height = 21
            if record.artificial_analysis_url:
                model_cell = sheet.cell(row=row_index, column=1)
                model_cell.hyperlink = record.artificial_analysis_url
                model_cell.font = Font(color="087F8C", underline="single")
                link_cell = sheet.cell(row=row_index, column=len(columns))
                link_cell.hyperlink = record.artificial_analysis_url
                link_cell.font = Font(color="087F8C", underline="single")

        sheet.auto_filter.ref = sheet.dimensions
        sheet.freeze_panes = "A2"
        for column_index, (header, _attribute, kind) in enumerate(columns, start=1):
            values = [
                len(str(sheet.cell(row=row_index, column=column_index).value or ""))
                for row_index in range(1, sheet.max_row + 1)
            ]
            maximum_width = 19 if kind in {"currency", "credits"} else 30
            sheet.column_dimensions[sheet.cell(row=1, column=column_index).column_letter].width = min(
                max(len(header), max(values, default=0)) + 2,
                maximum_width,
            )
        excel_buffer = io.BytesIO()
        workbook.save(excel_buffer)
        excel_bytes = excel_buffer.getvalue()
        _validate_workbook_bytes(excel_bytes)
        output_paths.append(_write_unique_file(
            destination, safe_prefix, ".xlsx", excel_bytes,
        ))

    return output_paths
