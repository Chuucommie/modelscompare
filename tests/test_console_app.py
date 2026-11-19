from decimal import Decimal
import json

import pytest

from modelscompare.console_app import _arguments, _restart_arguments, filter_records
from modelscompare.models import ModelPricing
from modelscompare.preferences import Preferences, save_preferences
from modelscompare.updates import UpdateInfo, UpdateResult


def _records() -> list[ModelPricing]:
    return [
        ModelPricing(
            name="Expensive input",
            input_cost_credits=Decimal("300"),
            output_cost_credits=Decimal("30"),
            cached_input_cost_credits=Decimal("50"),
            cache_write_cost_credits=Decimal("40"),
            usd_per_credit=Decimal("0.01"),
            provider="OpenAI",
            category="Powerful",
            tier="Default",
            release_status="GA",
            threshold="<= 272K",
        ),
        ModelPricing(
            name="Cheap input",
            input_cost_credits=Decimal("100"),
            output_cost_credits=Decimal("300"),
            cached_input_cost_credits=Decimal("10"),
            cache_write_cost_credits=Decimal("5"),
            usd_per_credit=Decimal("0.01"),
            provider="OpenAI",
            category="Lightweight",
            tier="Default",
            release_status="GA",
            threshold="Not applicable",
        ),
        ModelPricing(
            name="Different provider",
            input_cost_credits=Decimal("200"),
            output_cost_credits=Decimal("50"),
            cached_input_cost_credits=Decimal("15"),
            cache_write_cost_credits=Decimal("8"),
            usd_per_credit=Decimal("0.01"),
            provider="Anthropic",
            category="Powerful",
            tier="Long context",
            release_status="Preview",
            threshold="> 200K",
        ),
    ]


def test_cost_sort_uses_requested_cost_dimension_only() -> None:
    preferences = Preferences.defaults()
    preferences.sort_by = "cached_write"

    result = filter_records(_records(), preferences, _arguments([]))

    assert [record.name for record in result] == [
        "Cheap input", "Different provider", "Expensive input",
    ]


def test_setup_filters_and_command_overrides_combine() -> None:
    preferences = Preferences.defaults()
    preferences.providers = ["OpenAI"]
    preferences.categories = ["Powerful", "Lightweight"]
    preferences.release_statuses = ["GA"]
    args = _arguments(["--model", "input", "--tier", "default", "--sort", "output"])

    result = filter_records(_records(), preferences, args)

    assert [record.name for record in result] == ["Expensive input", "Cheap input"]
    assert [record.name for record in result] == sorted(
        ["Expensive input", "Cheap input"],
        key=lambda name: next(record.output_cost_usd for record in _records() if record.name == name),
    )


def test_catalog_view_ignores_legacy_saved_filters_but_keeps_explicit_cli_filters() -> None:
    preferences = Preferences.defaults()
    preferences.providers = ["OpenAI"]
    preferences.release_statuses = ["GA"]
    preferences.thresholds = ["<= 272K"]
    records = _records()

    all_records = filter_records(records, preferences, _arguments([]), include_saved_filters=False)
    explicit_filter = filter_records(
        records,
        preferences,
        _arguments(["--provider", "Anthropic"]),
        include_saved_filters=False,
    )

    assert {record.name for record in all_records} == {record.name for record in records}
    assert [record.name for record in explicit_filter] == ["Different provider"]


def test_restart_arguments_keep_view_options_and_drop_one_shot_actions(tmp_path) -> None:
    config = (tmp_path / "custom.json").resolve()
    selection = (tmp_path / "selection.json").resolve()
    args = _arguments([
        "--config-file", str(config),
        "--selection-file", str(selection),
        "--all-models",
        "--sort", "output",
        "--unit", "credits",
        "--limit", "5",
        "--model", "GPT",
        "--search", "reasoning",
        "--no-export",
        "--setup",
    ])

    assert _restart_arguments(args) == [
        "--config-file", str(config),
        "--selection-file", str(selection),
        "--all-models",
        "--no-export",
        "--sort", "output",
        "--unit", "credits",
        "--limit", "5",
        "--model", "GPT",
        "--search", "reasoning",
    ]


def test_blended_cost_cli_flags_are_removed() -> None:
    with pytest.raises(SystemExit):
        _arguments(["--max-cost", "100"])


def test_search_matches_metadata_and_combines_terms_with_and() -> None:
    result = filter_records(
        _records(),
        Preferences.defaults(),
        _arguments(["--search", "powerful", "--search", "openai"]),
        include_saved_filters=False,
    )

    assert [record.name for record in result] == ["Expensive input"]


def test_json_output_contains_each_cost_separately(monkeypatch, tmp_path, capsys) -> None:
    from modelscompare import console_app

    preferences = Preferences.defaults()
    preferences.setup_completed = True
    config_path = tmp_path / "config.json"
    save_preferences(preferences, config_path)
    record = ModelPricing(
        name="Example Model",
        input_cost_credits=Decimal("100"),
        output_cost_credits=Decimal("400"),
        cached_input_cost_credits=Decimal("10"),
        cache_write_cost_credits=Decimal("125"),
        usd_per_credit=Decimal("0.01"),
    )
    monkeypatch.setattr(console_app, "fetch_live_records", lambda: [record])

    assert console_app.main(["--config-file", str(config_path), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1
    model = payload["models"][0]
    assert model["input_cost_usd"] == 1.0
    assert model["cached_read_cost_usd"] == 0.1
    assert model["cached_write_cost_usd"] == 1.25
    assert model["output_cost_usd"] == 4.0
    assert not any("blended" in key for key in model)


def test_output_dir_is_forwarded_to_export(monkeypatch, tmp_path) -> None:
    from modelscompare import console_app

    preferences = Preferences.defaults()
    preferences.setup_completed = True
    preferences.export_formats = ["csv"]
    config_path = tmp_path / "config.json"
    save_preferences(preferences, config_path)
    record = ModelPricing("Example Model", Decimal("100"), Decimal("400"))
    calls = []

    def fake_export(records, formats, prefix, downloads_dir=None):
        calls.append((records, formats, prefix, downloads_dir))
        return []

    monkeypatch.setattr(console_app, "fetch_live_records", lambda: [record])
    monkeypatch.setattr(console_app, "export_records", fake_export)

    target = tmp_path / "reports"
    assert console_app.main([
        "--config-file", str(config_path), "--export", "--output-dir", str(target),
    ]) == 0
    assert calls == [([record], ["csv"], "copilot_models", target)]


def test_export_failure_returns_error_code_and_message(monkeypatch, tmp_path, capsys) -> None:
    from modelscompare import console_app

    preferences = Preferences.defaults()
    preferences.setup_completed = True
    preferences.export_formats = ["csv"]
    config_path = tmp_path / "config.json"
    save_preferences(preferences, config_path)
    record = ModelPricing("Example Model", Decimal("100"), Decimal("400"))

    def failing_export(*_args, **_kwargs):
        raise OSError("cartella non scrivibile")

    monkeypatch.setattr(console_app, "fetch_live_records", lambda: [record])
    monkeypatch.setattr(console_app, "export_records", failing_export)

    assert console_app.main(["--config-file", str(config_path), "--export"]) == 1
    assert "Export non riuscito" in capsys.readouterr().out


def test_version_flag_prints_package_version(capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        _arguments(["--version"])

    assert exit_info.value.code == 0
    assert "modelscompare 0.2.0" in capsys.readouterr().out


def test_check_updates_json_reports_no_release(monkeypatch, capsys) -> None:
    from modelscompare import console_app

    monkeypatch.setattr(console_app, "check_for_update", lambda: None)

    assert console_app.main(["--check-updates", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["current"] == "0.2.0"
    assert payload["update_available"] is False


def test_update_command_reports_pip_instructions_for_source_installs(monkeypatch, capsys) -> None:
    from modelscompare import console_app

    info = UpdateInfo(
        current="0.2.0",
        latest="v0.3.0",
        tag="v0.3.0",
        html_url="https://example.test/release",
        is_newer=True,
    )
    monkeypatch.setattr(console_app, "check_for_update", lambda: info)
    monkeypatch.setattr(
        console_app,
        "install_update",
        lambda _info, restart_args=(): UpdateResult(False, "usa python -m pip install --upgrade modelscompare"),
    )

    assert console_app.main(["--update"]) == 2
    assert "pip install" in capsys.readouterr().out
