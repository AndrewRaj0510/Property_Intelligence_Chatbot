"""Edge cases for list_projects (offline, SQLite only). Expected values come straight from the database."""
import json
import sqlite3

from app import config, tools


def _rows():
    con = sqlite3.connect(config.SQLITE_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM projects").fetchall()
    con.close()
    return rows


def _names(**kw) -> set[str]:
    out = tools.list_projects(tools.ListProjectsArgs(**kw))
    return {s["title"] for s in out.sources if s["url"]}


def _expect(pred) -> set[str]:
    return {r["name"] for r in _rows() if pred(r)}


def _brands(r):
    return [b.lower() for b in json.loads(r["brand_partner"] or "[]")]


def _types(r):
    return json.loads(r["property_types"] or "[]")


def test_no_filter_is_complete():
    assert _names() == _expect(lambda r: True) and len(_names()) == len(_rows())


def test_country_aliases():
    uae = _expect(lambda r: r["country"] == "United Arab Emirates")
    for v in ["UAE", "uae", "Emirates", "United Arab Emirates", "dubai", "RAK"]:
        assert _names(country=v) == uae, v
    ksa = _expect(lambda r: r["country"] == "Saudi Arabia")
    for v in ["KSA", "Saudi", "saudi arabia", "Kingdom of Saudi Arabia"]:
        assert _names(country=v) == ksa, v
    assert _names(country="England") == _expect(lambda r: r["country"] == "United Kingdom")
    assert len(_names(country="Oman")) == 13


def test_regions():
    gcc = {"United Arab Emirates", "Saudi Arabia", "Oman", "Qatar"}
    assert _names(country="GCC") == _expect(lambda r: r["country"] in gcc)
    assert _names(country="Middle East") == _names(country="gcc")
    assert _names(country="Europe") == _expect(lambda r: r["country"] in {"Spain", "United Kingdom"})


def test_city_aliases():
    assert _names(city="Marbella") == _expect(lambda r: r["city"] in {"Benahavis", "Casares"})
    assert _names(city="Ras Al Khaimah") == _expect(lambda r: r["city"] == "Al Marjan Island")
    assert _names(city="riyadh") == _expect(lambda r: r["city"] in {"Riyadh", "Wadi Safar"})
    assert _names(city="AIDA") == _expect(lambda r: r["city"] == "Muscat")


def test_brands():
    assert _names(brand="Trump") == _names(brand="trump") == _expect(lambda r: "trump" in _brands(r))
    assert _names(brand="Fendi") == _expect(lambda r: "fendi casa" in _brands(r))
    assert _names(brand="W") == _expect(lambda r: "w hotels" in _brands(r) or "w residences" in _brands(r))
    assert _names(brand="aston martin") == _expect(lambda r: "aston martin" in _brands(r))
    assert _names(brand="Nobu") == set()


def test_branded_split_is_complete():
    b, nb = _names(branded_only=True), _names(branded_only=False)
    assert b and nb and not (b & nb) and len(b | nb) == len(_rows())


def test_property_type_synonyms_and_plurals():
    assert _names(property_type="villas") == _names(property_type="Villa") == _expect(lambda r: "villa" in _types(r))
    assert _names(property_type="flats") == _expect(lambda r: "apartment" in _types(r))
    assert _names(property_type="penthouses") == _expect(lambda r: "penthouse" in _types(r))
    assert _names(property_type="land") == _expect(lambda r: "plot" in _types(r))
    assert _names(property_type="hotel apartments") == _expect(lambda r: "hotel room" in _types(r))


def test_status_synonyms():
    assert _names(status="off-plan") == _names(status="under construction") == _expect(lambda r: r["status"] == "Under Development")
    assert _names(status="ready") == _expect(lambda r: r["status"] in {"Ready", "Completed"})
    assert _names(status="sold out") == _expect(lambda r: r["status"] == "Sold Out")


def test_collection_and_name():
    assert _names(collection="one of one") == _expect(lambda r: r["collection"] == "One of One")
    assert _names(name_contains="trump") == _expect(lambda r: "trump" in r["name"].lower())


def test_combined_filters():
    assert _names(country="KSA", brand="Trump") == _expect(lambda r: r["country"] == "Saudi Arabia" and "trump" in _brands(r))
    assert _names(country="Oman", property_type="villas") == _expect(lambda r: r["country"] == "Oman" and "villa" in _types(r))


def test_unknown_value_lists_what_exists():
    out = tools.list_projects(tools.ListProjectsArgs(country="Egypt"))
    assert out.meta["total"] == 0 and "Values that exist" in out.text and "Oman" in out.text


def test_price_filter_reported_unsupported():
    out = tools.list_projects(tools.ListProjectsArgs(country="UAE", max_price=2_000_000))
    assert '"unsupported_filters": ["max_price"]' in out.text
    assert out.meta["total"] == len(_expect(lambda r: r["country"] == "United Arab Emirates"))


def test_every_row_named_with_url():
    out = tools.list_projects(tools.ListProjectsArgs())
    rows = [ln for ln in out.text.splitlines() if ln.startswith("[")]
    assert len(rows) == len(_rows()) and all("NAME: " in ln for ln in rows)
    assert out.text.count("URL: https://darglobal.co.uk") == len(_rows())


def test_run_tool_validation():
    assert tools.run_tool("list_projects", '{"country": "UAE"}').meta["total"] > 0
    assert tools.run_tool("list_projects", '{"min_price": -5}').text.startswith("ERROR: invalid arguments")
    assert tools.run_tool("list_projects", "not json").text.startswith("ERROR: invalid arguments")


def test_combined_values_from_the_model():
    # Regression: the model copied "Saudi Arabia/KSA" and "off-plan/under development" from field descriptions
    # and the tool answered "0 projects".
    assert _names(country="Saudi Arabia/KSA") == _expect(lambda r: r["country"] == "Saudi Arabia")
    assert _names(city="Dubai", status="off-plan/under development") == _expect(
        lambda r: r["city"] == "Dubai" and r["status"] == "Under Development")
    assert _names(property_type="villa or townhouse") == _expect(lambda r: {"villa", "townhouse"} & set(_types(r)))
    assert _names(country="Oman, Qatar") == _expect(lambda r: r["country"] in {"Oman", "Qatar"})


def test_unknown_value_is_explicit_not_silent_zero():
    out = tools.list_projects(tools.ListProjectsArgs(country="Egypt"))
    assert "do not exist in DarGlobal's project data" in out.text and '"egypt"' in out.text.lower()
    out = tools.list_projects(tools.ListProjectsArgs(country="UAE"))
    assert "do not exist" not in out.text


def test_full_list_fits_the_tool_output_cap():
    from app import guardrail
    out = tools.list_projects(tools.ListProjectsArgs())
    content, _ = guardrail.sanitize_tool_text("list_projects", out.text, config.TOOL_OUTPUT_CHARS_LIST)
    assert "[truncated]" not in content and content.count("NAME: ") == len(_rows())
