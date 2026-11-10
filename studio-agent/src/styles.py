"""Optional business style presets and validated speech-delivery overrides."""

import json
import math
from copy import deepcopy
from pathlib import Path

PRESETS_PATH = Path(__file__).resolve().parent.parent / "style_presets.json"
SOUNDS = {
    "laughing",
    "breathing",
    "sighing",
    "crying",
    "vocalizing",
    "mouth_sounds",
    "reflex_sounds",
}


def merge_settings(base: dict, overrides: dict) -> dict:
    """Nested overrides keep unspecified preset settings; never mutate a preset."""
    result = deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_settings(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def load_style(
    config: dict, *, slug: str, tts_model: str
) -> tuple[str, dict, bool | dict]:
    """No style selected preserves the previous expressive=True behavior.

    Voice/model stay in client.json. Numeric speed/volume currently target Fish
    Audio only; reject incompatible options rather than silently misapply them.
    """

    def fail(message):
        raise ValueError(f"Client '{slug}': {message}")

    preset = {}
    if "style" in config:
        presets = json.loads(PRESETS_PATH.read_text())
        name = config["style"]
        if not isinstance(name, str) or name not in presets:
            fail(f"style must be one of {', '.join(presets)}; omit it to keep defaults")
        preset = presets[name]

    tts_options = config.get("tts_options", {})
    if not isinstance(tts_options, dict) or set(tts_options) - {"speed", "volume"}:
        fail("tts_options must be an object with only speed and/or volume")
    if tts_options and not tts_model.startswith("fishaudio/"):
        fail("tts_options speed/volume currently require a Fish Audio tts_model")
    for key, value in tts_options.items():
        lower, upper = (0.5, 2.0) if key == "speed" else (-20, 20)
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not lower <= value <= upper
        ):
            fail(f"tts_options.{key} must be a number from {lower} to {upper}")

    expressive = deepcopy(preset.get("expressive", True))
    if "expressive" in config:
        override = config["expressive"]
        if isinstance(override, bool):
            expressive = override
        elif isinstance(override, dict):
            expressive = merge_settings(
                expressive if isinstance(expressive, dict) else {}, override
            )
        else:
            fail("expressive must be true, false, or a settings object")

    if isinstance(expressive, dict):
        if set(expressive) - {"speech_steering", "tts_instructions_append"}:
            fail("expressive supports only speech_steering and tts_instructions_append")
        if not isinstance(expressive.get("tts_instructions_append", ""), str):
            fail("expressive.tts_instructions_append must be text")
        steering = expressive.get("speech_steering", {})
        if not isinstance(steering, dict) or set(steering) - {
            "pace",
            "disfluencies",
            "nonverbal_sounds",
        }:
            fail(
                "expressive.speech_steering supports pace, disfluencies and nonverbal_sounds"
            )
        if "pace" in steering and steering["pace"] not in ("slow", "normal", "fast"):
            fail("expressive.speech_steering.pace must be slow, normal or fast")
        if "disfluencies" in steering and not isinstance(
            steering["disfluencies"], bool
        ):
            fail("expressive.speech_steering.disfluencies must be true or false")
        sounds = steering.get("nonverbal_sounds", True)
        if not isinstance(sounds, bool) and (
            not isinstance(sounds, dict)
            or set(sounds) - SOUNDS
            or not all(isinstance(value, bool) for value in sounds.values())
        ):
            fail(
                "expressive.speech_steering.nonverbal_sounds must be a boolean or supported sound/boolean pairs"
            )
    return preset.get("demo_instructions", ""), deepcopy(tts_options), expressive
