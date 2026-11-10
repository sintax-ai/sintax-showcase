"""Load a client business's configuration folder.

The engine (src/) is shared by every client; everything specific to one business
lives in clients/<slug>/:

    client.json      name, business_id, greeting, voice, services, timezone,
                     optional model overrides (stt_model, llm_model, tts_model, language),
                     style preset, tts_options, expressive delivery overrides
    knowledge.md     the facts the receptionist may use
    instructions.md  how it behaves for this business
    demo_instructions.md  optional demo wording, intake and handoff customization

Which client runs must be chosen explicitly with SINTAX_CLIENT (or a loader
argument); there is no default business. Model choices default to the one tested stack; override
per client only with a reason, because every different stack is a separate
thing to test and debug.
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from styles import load_style

# The tested default stack. Change here = change for every client without an override.
DEFAULTS = {
    "stt_model": "assemblyai/universal-3-5-pro",
    "llm_model": "google/gemma-4-31b-it",
    "tts_model": "fishaudio/s2.1-pro",
    "language": "en",
}

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SERVICE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,49}$")  # matches the database rule

CLIENTS_ROOT = Path(__file__).resolve().parent.parent / "clients"


@dataclass(frozen=True)
class ClientConfig:
    slug: str
    name: str
    business_id: str
    greeting: str
    voice: str
    timezone: str
    services: frozenset
    stt_model: str
    llm_model: str
    tts_model: str
    language: str
    instructions: str
    knowledge: str
    demo_instructions: str = ""
    demo_style_instructions: str = ""
    tts_options: dict = field(default_factory=dict)
    expressive: bool | dict = True

    @property
    def prompt(self) -> str:
        # Keep this join byte-identical to the pre-refactor prompt so agent
        # behavior does not shift. Revisit the "STUDIO FACTS" header when the
        # first non-studio client arrives (retest behavior when you do).
        return self.instructions + "\n\nSTUDIO FACTS:\n" + self.knowledge


def load_client(slug: str | None = None, root: Path | None = None) -> ClientConfig:
    root = root or CLIENTS_ROOT
    slug = os.getenv("SINTAX_CLIENT") if slug is None else slug
    if not slug or not slug.strip():
        raise ValueError(
            "Set SINTAX_CLIENT to a client folder name (for example, sampleclub or "
            "sample-studio); no default client is selected."
        )
    folder = root / slug
    if not (folder / "client.json").is_file():
        available = (
            sorted(p.name for p in root.iterdir() if (p / "client.json").is_file())
            if root.is_dir()
            else []
        )
        raise ValueError(
            f"No client '{slug}' in {root}. Available: {', '.join(available) or 'none'}"
        )
    texts = {}
    for name in ("knowledge.md", "instructions.md"):
        path = folder / name
        if not path.is_file():
            raise ValueError(f"Client '{slug}' is missing {name}")
        texts[name] = path.read_text()

    config = json.loads((folder / "client.json").read_text())
    merged = DEFAULTS | config

    timezone = config.get("timezone")
    try:
        if not isinstance(timezone, str) or not timezone:
            raise ValueError("missing timezone")
        ZoneInfo(timezone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError(
            f"Client '{slug}': timezone must be a valid IANA name, "
            "for example America/New_York; there is no default timezone"
        ) from exc

    business_id = str(merged.get("business_id", "")).lower()
    if not UUID.match(business_id):
        raise ValueError(f"Client '{slug}': business_id must be the business's UUID")
    services = merged.get("services") or []
    if not services or not all(
        isinstance(s, str) and SERVICE_CODE.match(s) for s in services
    ):
        raise ValueError(
            f"Client '{slug}': services must be non-empty lowercase codes like 'recording'"
        )
    for key in ("name", "greeting", "voice"):
        if not str(merged.get(key, "")).strip():
            raise ValueError(f"Client '{slug}': {key} must not be empty")

    demo_path = folder / "demo_instructions.md"
    demo_instructions = demo_path.read_text() if demo_path.is_file() else ""
    demo_style, tts_options, expressive = load_style(
        config, slug=slug, tts_model=merged["tts_model"]
    )

    return ClientConfig(
        slug=slug,
        name=merged["name"],
        business_id=business_id,
        greeting=merged["greeting"],
        voice=merged["voice"],
        timezone=timezone,
        services=frozenset(services),
        stt_model=merged["stt_model"],
        llm_model=merged["llm_model"],
        tts_model=merged["tts_model"],
        language=merged["language"],
        instructions=texts["instructions.md"],
        knowledge=texts["knowledge.md"],
        demo_instructions=demo_instructions,
        demo_style_instructions=demo_style,
        tts_options=tts_options,
        expressive=expressive,
    )
