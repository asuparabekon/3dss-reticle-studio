"""Switch-state settings shared by the converter, descriptor and Studio."""

from dataclasses import dataclass
import json
import math

MAX_STATES = 15


@dataclass(frozen=True)
class MaterialOverride:
    material: str
    colour: tuple | None = None
    brightness: float = 1.0
    visible: bool = True


@dataclass(frozen=True)
class SwitchState:
    pattern: int
    colour: tuple | None = None  # None preserves each illuminated SVG fill.
    brightness: float = 1.0
    materials: tuple = ()


def appearance(row):
    if not isinstance(row, dict):
        raise ValueError("Appearance settings must be an object")
    brightness = row.get("brightness", 1)
    if (
        not isinstance(brightness, (int, float))
        or not math.isfinite(brightness)
        or not 0 <= brightness <= 1
    ):
        raise ValueError("Brightness must be between 0 and 1")
    colour = row.get("colour")
    if colour is not None:
        if (
            not isinstance(colour, (list, tuple))
            or len(colour) != 3
            or any(
                not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1
                for v in colour
            )
        ):
            raise ValueError("Colour needs three components between 0 and 1")
        colour = tuple(colour)
    return colour, float(brightness)


def parse_states(text):
    values = json.loads(text) if text.strip() else []
    if not isinstance(values, list) or len(values) > MAX_STATES:
        raise ValueError("Switch states must be a list of at most 15 entries")
    result = []
    for row in values:
        colour, brightness = appearance(row)
        pattern = row.get("pattern")
        if not isinstance(pattern, int) or isinstance(pattern, bool) or not 1 <= pattern <= 15:
            raise ValueError("Each switch state needs a pattern number from 1 to 15")
        overrides = []
        materials = row.get("materials", {})
        if not isinstance(materials, dict) or len(materials) > 16:
            raise ValueError("A state supports at most 16 named materials")
        for material, value in materials.items():
            material_colour, material_brightness = appearance(value)
            visible = value.get("visible", True)
            if not isinstance(visible, bool):
                raise ValueError("Material visibility must be true or false")
            overrides.append(
                MaterialOverride(material, material_colour, material_brightness, visible)
            )
        result.append(SwitchState(pattern, colour, brightness, tuple(overrides)))
    return tuple(result)


def states_text(states):
    return json.dumps(
        [
            dict(
                pattern=s.pattern,
                colour=s.colour,
                brightness=s.brightness,
                materials={
                    m.material: dict(colour=m.colour, brightness=m.brightness, visible=m.visible)
                    for m in s.materials
                },
            )
            for s in states
        ]
    )


def resolve_states(settings, patterns):
    patterns = sorted(set(patterns) - {0}) or [1]
    states = settings.switch_states or tuple(SwitchState(p) for p in patterns)
    for state in states:
        if state.pattern not in patterns:
            raise ValueError(f"Switch state refers to missing pattern {state.pattern}")
    return tuple(states)


def material_value(state, material):
    """A group's explicit override, or the previous format's illumination defaults."""
    for value in state.materials:
        if value.material == material.key:
            return value
    return MaterialOverride(
        material.key,
        state.colour if material.role == "illum" else None,
        state.brightness if material.role == "illum" else 1.0,
    )
