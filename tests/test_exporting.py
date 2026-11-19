import csv
import zipfile
from decimal import Decimal

from openpyxl import load_workbook

from modelscompare.exporting import export_records
from modelscompare.models import ModelPricing


def _record() -> ModelPricing:
    return ModelPricing(
        name="GPT Example",
        input_cost_credits=Decimal("100"),
        output_cost_credits=Decimal("200"),
        cached_input_cost_credits=Decimal("10"),
        cache_write_cost_credits=Decimal("125"),
        usd_per_credit=Decimal("0.01"),
        provider="OpenAI",
        artificial_analysis_url="https://artificialanalysis.ai/models/gpt-example",
    )


def test_csv_and_excel_export_only_the_four_cost_types(tmp_path) -> None:
    paths = export_records([_record()], ["csv", "xlsx"], downloads_dir=tmp_path)
    csv_path, excel_path = paths

    with csv_path.open(newline="", encoding="utf-8-sig") as file:
        row = next(csv.DictReader(file))
    assert row["Input (USD / 1M token)"] == "1.00"
    assert row["Cached read (USD / 1M token)"] == "0.10"
    assert row["Cached write (USD / 1M token)"] == "1.25"
    assert row["Output (USD / 1M token)"] == "2.00"
    assert all("_" not in header for header in row)
    assert not any("blended" in header.casefold() for header in row)

    sheet = load_workbook(excel_path).active
    headers = [cell.value for cell in sheet[1]]
    input_usd_column = headers.index("Input (USD / 1M token)") + 1
    cached_read_credits_column = headers.index("Cached read (crediti AI / 1M token)") + 1
    link_column = headers.index("Scheda Artificial Analysis") + 1
    assert sheet.cell(2, input_usd_column).value == 1
    assert sheet.cell(2, input_usd_column).data_type == "n"
    assert sheet.cell(2, input_usd_column).number_format.startswith("$")
    assert sheet.cell(2, cached_read_credits_column).value == 10
    assert sheet.cell(2, cached_read_credits_column).number_format == '#,##0.00 "cr"'
    assert sheet.cell(2, link_column).hyperlink.target.endswith("/gpt-example")
    assert "Ragionamento" in headers
    assert "Finestra contesto" in headers
    assert list(sheet.tables) == []
    assert sheet.auto_filter.ref == sheet.dimensions
    assert not any("blended" in header.casefold() for header in headers)


def test_excel_export_survives_edit_and_filter_round_trip(tmp_path) -> None:
    excel_path = export_records([_record()], ["xlsx"], downloads_dir=tmp_path)[0]

    workbook = load_workbook(excel_path)
    sheet = workbook.active
    sheet["A2"] = "GPT Example edited"
    sheet.auto_filter.add_filter_column(0, ["GPT Example edited"])
    workbook.save(excel_path)

    workbook = load_workbook(excel_path)
    sheet = workbook.active
    assert sheet["A2"].value == "GPT Example edited"
    assert sheet.auto_filter.ref == sheet.dimensions
    assert list(sheet.tables) == []
    assert sheet.auto_filter.filterColumn[0].filters.filter == ["GPT Example edited"]


def test_excel_export_is_a_valid_zip_and_stays_readable_with_no_rows(tmp_path) -> None:
    excel_path = export_records([], ["xlsx"], downloads_dir=tmp_path)[0]

    with zipfile.ZipFile(excel_path) as bundle:
        assert bundle.testzip() is None
        assert "xl/workbook.xml" in bundle.namelist()

    sheet = load_workbook(excel_path, read_only=True).active
    assert sheet.max_row == 1
    assert sheet.max_column == len(sheet[1])


def test_excel_export_removes_characters_that_would_corrupt_the_workbook(tmp_path) -> None:
    record = ModelPricing(
        name="Bad\x00Model\x07",
        input_cost_credits=Decimal("100"),
        output_cost_credits=Decimal("200"),
        usd_per_credit=Decimal("0.01"),
        provider="OpenAI\x1f",
    )

    excel_path = export_records([record], ["xlsx"], downloads_dir=tmp_path)[0]
    sheet = load_workbook(excel_path).active

    assert sheet["A2"].value == "BadModel"
    assert sheet["B2"].value == "OpenAI"


def test_export_respects_selected_formats_and_downloads_directory(tmp_path) -> None:
    paths = export_records([_record()], ["csv"], "nested/only_csv.xlsx", tmp_path)

    assert len(paths) == 1
    assert paths[0].name == "only_csv.csv"
    assert paths[0].parent == tmp_path


def test_excel_tiny_costs_use_enough_decimals_to_stay_readable(tmp_path) -> None:
    record = ModelPricing(
        name="Tiny",
        input_cost_credits=Decimal("0.000001"),
        output_cost_credits=Decimal("0.000001"),
        usd_per_credit=Decimal("0.01"),
    )
    excel_path = export_records([record], ["xlsx"], downloads_dir=tmp_path)[0]
    sheet = load_workbook(excel_path).active
    headers = [cell.value for cell in sheet[1]]
    usd_column = headers.index("Input (USD / 1M token)") + 1
    credits_column = headers.index("Input (crediti AI / 1M token)") + 1

    assert "0.00000000" in sheet.cell(2, usd_column).number_format
    assert '0.000000 "cr"' in sheet.cell(2, credits_column).number_format


def test_export_uses_incremental_suffixes_without_overwriting_existing_files(tmp_path) -> None:
    export_records([_record()], ["csv", "xlsx"], downloads_dir=tmp_path)
    export_records([_record()], ["csv", "xlsx"], downloads_dir=tmp_path)

    paths = export_records([_record()], ["csv", "xlsx"], downloads_dir=tmp_path)

    assert [path.name for path in paths] == ["copilot_models_2.csv", "copilot_models_2.xlsx"]
    assert load_workbook(paths[1]).active["A2"].value == "GPT Example"
