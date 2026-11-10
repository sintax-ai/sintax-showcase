"""Per-client configuration loading. All example clients here are FICTIONAL."""

import json

import pytest

from clients import DEFAULTS, load_client

BUSINESS = "d00c0c1d-7687-5992-bfbd-b69a214c566e"


def write_client(root, slug, config=None, knowledge="Facts.", instructions="Behave."):
    folder = root / slug
    folder.mkdir(parents=True)
    base = {
        "name": "FICTIONAL Business",
        "business_id": BUSINESS,
        "greeting": "Hi, this is Alex from FICTIONAL Business. How can I help?",
        "voice": "fictional-voice-id",
        "timezone": "America/New_York",
        "services": ["repair", "install"],
    }
    (folder / "client.json").write_text(json.dumps(base | (config or {})))
    (folder / "knowledge.md").write_text(knowledge)
    (folder / "instructions.md").write_text(instructions)
    return folder


def test_loads_a_client_folder(tmp_path):
    write_client(tmp_path, "fictional-hvac")
    c = load_client("fictional-hvac", root=tmp_path)
    assert c.slug == "fictional-hvac"
    assert c.name == "FICTIONAL Business"
    assert c.business_id == BUSINESS
    assert c.services == {"repair", "install"}
    assert c.voice == "fictional-voice-id"
    assert "Alex" in c.greeting


def test_prompt_joins_instructions_and_knowledge_verbatim(tmp_path):
    write_client(tmp_path, "x", knowledge="K-FACTS", instructions="I-RULES")
    c = load_client("x", root=tmp_path)
    assert c.prompt == "I-RULES\n\nSTUDIO FACTS:\nK-FACTS"


def test_model_defaults_are_the_tested_stack(tmp_path):
    write_client(tmp_path, "x")
    c = load_client("x", root=tmp_path)
    assert c.stt_model == DEFAULTS["stt_model"]
    assert c.llm_model == DEFAULTS["llm_model"]
    assert c.tts_model == DEFAULTS["tts_model"]
    assert c.language == "en"


def test_models_can_be_overridden_per_client(tmp_path):
    write_client(tmp_path, "x", {"llm_model": "other/model", "language": "es"})
    c = load_client("x", root=tmp_path)
    assert c.llm_model == "other/model"
    assert c.language == "es"
    assert c.stt_model == DEFAULTS["stt_model"]  # untouched fields keep defaults


def test_unknown_client_lists_available(tmp_path):
    write_client(tmp_path, "fictional-hvac")
    with pytest.raises(ValueError, match="fictional-hvac"):
        load_client("nope", root=tmp_path)


@pytest.mark.parametrize(
    "bad",
    [
        {"business_id": "not-a-uuid"},
        {"greeting": ""},
        {"services": []},
        {"services": ["Bad Service!"]},
        {"voice": ""},
    ],
)
def test_invalid_config_is_refused_with_field_name(tmp_path, bad):
    write_client(tmp_path, "x", bad)
    with pytest.raises(ValueError, match=next(iter(bad))):
        load_client("x", root=tmp_path)


def test_missing_files_are_refused(tmp_path):
    folder = write_client(tmp_path, "x")
    (folder / "knowledge.md").unlink()
    with pytest.raises(ValueError, match=r"knowledge\.md"):
        load_client("x", root=tmp_path)


def test_env_var_selects_the_client(tmp_path, monkeypatch):
    write_client(tmp_path, "a", {"name": "A"})
    write_client(tmp_path, "b", {"name": "B"})
    monkeypatch.setenv("SINTAX_CLIENT", "b")
    assert load_client(root=tmp_path).name == "B"


@pytest.mark.parametrize("selection", [None, "", "   "])
def test_missing_selection_never_chooses_sample_studio(tmp_path, monkeypatch, selection):
    write_client(tmp_path, "sample-studio")
    if selection is None:
        monkeypatch.delenv("SINTAX_CLIENT", raising=False)
    else:
        monkeypatch.setenv("SINTAX_CLIENT", selection)
    with pytest.raises(ValueError, match="SINTAX_CLIENT"):
        load_client(root=tmp_path)


def test_explicit_client_wins_over_environment(tmp_path, monkeypatch):
    write_client(tmp_path, "a", {"name": "A"})
    write_client(tmp_path, "b", {"name": "B"})
    monkeypatch.setenv("SINTAX_CLIENT", "b")
    assert load_client("a", root=tmp_path).name == "A"


def test_blank_explicit_selection_does_not_use_environment(tmp_path, monkeypatch):
    write_client(tmp_path, "sample-studio")
    monkeypatch.setenv("SINTAX_CLIENT", "sample-studio")
    with pytest.raises(ValueError, match="SINTAX_CLIENT"):
        load_client("", root=tmp_path)


def test_real_sample_studio_folder_loads():
    c = load_client("sample-studio")
    assert c.name == "Example Studios"
    assert c.business_id == BUSINESS
    assert "recording" in c.services and len(c.services) == 7
    assert "Sophia" in c.greeting
    assert "Example Studios" in c.prompt  # knowledge/instructions made it in


@pytest.mark.parametrize("timezone", [None, "", "Mars/Olympus", "../UTC", 123])
def test_invalid_timezone_stops_startup(tmp_path, timezone):
    write_client(tmp_path, "x", {"timezone": timezone})
    with pytest.raises(ValueError, match="timezone"):
        load_client("x", root=tmp_path)


def test_missing_timezone_has_no_implicit_default(tmp_path):
    folder = write_client(tmp_path, "x")
    path = folder / "client.json"
    config = json.loads(path.read_text())
    del config["timezone"]
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="timezone"):
        load_client("x", root=tmp_path)


def test_timezone_is_specific_to_each_client(tmp_path):
    write_client(tmp_path, "east", {"timezone": "America/New_York"})
    write_client(tmp_path, "west", {"timezone": "America/Los_Angeles"})
    assert load_client("east", root=tmp_path).timezone == "America/New_York"
    assert load_client("west", root=tmp_path).timezone == "America/Los_Angeles"


def test_sample_studio_services_match_portal_menu():
    """The voice agent and the portal must agree on the service codes."""
    import re
    from pathlib import Path

    c = load_client("sample-studio")
    mjs = (
        Path(__file__).resolve().parents[2]
        / "client-portal"
        / "public"
        / "services.mjs"
    )
    section = (
        mjs.read_text().split("'sample-studio': [", 1)[1].split("\n  ],", 1)[0]
    )
    portal = set(re.findall(r"\['([a-z0-9_]+)'", section))
    assert portal == set(c.services)
