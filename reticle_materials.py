"""SVG material identities and per-state values, independent of atlas tile splitting."""

from dataclasses import dataclass
from functools import lru_cache
import json

from reticle_states import material_value

MAX_MATERIALS = 16


@dataclass(frozen=True)
class Material:
    key: str
    name: str
    role: str
    pattern: int


def identity(shape):
    role = "paint" if shape.role == "ink" else shape.role
    return json.dumps((shape.mark, role, shape.material), separators=(",", ":"))


def discover(drawing):
    groups = {}
    for shape in drawing.shapes:
        key = identity(shape)
        role = "paint" if shape.role == "ink" else shape.role
        groups[key] = Material(key, shape.material_name or role, role, shape.mark)
    result = tuple(groups[k] for k in sorted(groups))
    if len(result) > MAX_MATERIALS:
        raise ValueError(
            f"The SVG has {len(result)} materials; the atlas supports at most {MAX_MATERIALS}. Combine groups that do not need separate controls."
        )
    return result


@lru_cache(maxsize=8)
def from_source(source):
    from svg_reticle import read_reticle_svg

    return discover(read_reticle_svg(source))


def state_values(states, materials):
    """Resolve named project overrides into the DDS's ordered material table."""
    known = {m.key for m in materials}
    for state in states:
        for override in state.materials:
            if override.material not in known:
                raise ValueError(
                    "A saved material override no longer exists in the SVG. Remove the missing material in the state editor."
                )
    return tuple(tuple(material_value(s, m) for m in materials) for s in states)
