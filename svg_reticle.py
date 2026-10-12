"""SVG reticle reader: parses, normalises and classifies the filled shapes of a reticle drawing.

Named Inkscape layers assign roles: "ink" (etched black), "illum" (emissive),
"paint" (the SVG fill colour). Shapes outside role layers are errors; fill colours never select a role.
A layer labelled "calibrate 10 MOA" (or "calibrate 2 mil") holds one straight line of that length, and a layer
labelled "center" a small mark on the bore centre; neither is drawn. Layers labelled "mark 1", "mark 2"... hold
the reticle patterns the sight's Mark Switch cycles through; everything outside them is shown with every mark.
Gradients, patterns, masks, clip paths, filters, strokes and partial opacity are reported as errors.

Geometry comes out as contours of segments in document user units, y pointing down:
  ("L", p0, p1)  ("Q", p0, c, p1)  ("C", p0, c0, c1, p1)
"""

import math
import re
import xml.etree.ElementTree as ET

SVG_NS = "{http://www.w3.org/2000/svg}"
INKSCAPE_NS = "{http://www.inkscape.org/namespaces/inkscape}"

LAYER_ROLES = ("ink", "illum", "paint")


class ReticleSvgError(ValueError):
    pass


# --- transforms -------------------------------------------------------------------------------


def mat_mul(a, b):
    return (
        a[0] * b[0] + a[2] * b[1],
        a[1] * b[0] + a[3] * b[1],
        a[0] * b[2] + a[2] * b[3],
        a[1] * b[2] + a[3] * b[3],
        a[0] * b[4] + a[2] * b[5] + a[4],
        a[1] * b[4] + a[3] * b[5] + a[5],
    )


def apply(m, p):
    return (m[0] * p[0] + m[2] * p[1] + m[4], m[1] * p[0] + m[3] * p[1] + m[5])


IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def parse_transform(text):
    m = IDENTITY
    text = text or ""
    end = 0
    arities = {
        "matrix": (6,),
        "translate": (1, 2),
        "scale": (1, 2),
        "rotate": (1, 3),
        "skewX": (1,),
        "skewY": (1,),
    }
    for match in re.finditer(r"(\w+)\s*\(([^)]*)\)", text):
        if text[end : match.start()].strip(" \t\r\n,"):
            raise ReticleSvgError("invalid transform syntax")
        end = match.end()
        name, args = match.groups()
        v = [finite_number(x) for x in numeric_tokens(args)]
        if name not in arities or len(v) not in arities[name]:
            raise ReticleSvgError(f"invalid {name} transform arguments")
        if name == "matrix":
            t = tuple(v)
        elif name == "translate":
            t = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0)
        elif name == "scale":
            t = (v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        elif name == "rotate":
            a = math.radians(v[0])
            t = (math.cos(a), math.sin(a), -math.sin(a), math.cos(a), 0, 0)
            if len(v) == 3:
                t = mat_mul(mat_mul((1, 0, 0, 1, v[1], v[2]), t), (1, 0, 0, 1, -v[1], -v[2]))
        elif name == "skewX":
            t = (1, 0, math.tan(math.radians(v[0])), 1, 0, 0)
        elif name == "skewY":
            t = (1, math.tan(math.radians(v[0])), 0, 1, 0, 0)
        else:
            raise ReticleSvgError(f"unsupported transform {name}")
        m = mat_mul(m, t)
    if text[end:].strip(" \t\r\n,") or not all(math.isfinite(v) for v in m):
        raise ReticleSvgError("invalid transform")
    return m


# --- style ------------------------------------------------------------------------------------


def style_of(el):
    style = {}
    for key in (
        "fill",
        "fill-opacity",
        "opacity",
        "stroke",
        "stroke-opacity",
        "fill-rule",
        "mask",
        "clip-path",
        "filter",
        "display",
        "visibility",
    ):
        if el.get(key) is not None:
            style[key] = el.get(key).strip()
    for item in (el.get("style") or "").split(";"):
        if ":" in item:
            k, v = item.split(":", 1)
            style[k.strip()] = v.strip()
    return style


def parse_colour(text):
    text = text.strip().lower()
    named = {
        "red": (255, 0, 0),
        "blue": (0, 0, 255),
        "black": (0, 0, 0),
        "white": (255, 255, 255),
        "lime": (0, 255, 0),
        "green": (0, 128, 0),
        "yellow": (255, 255, 0),
    }
    if text in named:
        return named[text]
    m = re.fullmatch(r"#([0-9a-f]{6})", text)
    if m:
        h = m.group(1)
        return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))
    m = re.fullmatch(r"#([0-9a-f]{3})", text)
    if m:
        return tuple(int(c * 2, 16) for c in m.group(1))
    m = re.fullmatch(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", text)
    if m:
        colour = tuple(int(x) for x in m.groups())
        if all(0 <= value <= 255 for value in colour):
            return colour
    raise ReticleSvgError(f"unsupported fill colour {text!r}")


def opacity_value(style, key):
    text = style.get(key, "1")
    value = finite_number(text[:-1]) / 100 if text.endswith("%") else finite_number(text)
    if not 0 <= value <= 1:
        raise ReticleSvgError(f"{key} must be between 0 and 1")
    return value


# --- path data --------------------------------------------------------------------------------

TOKEN = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def finite_number(text):
    value = float(text)
    if not math.isfinite(value):
        raise ReticleSvgError("coordinates and values must be finite")
    return value


def numeric_tokens(text, pattern=NUMBER):
    tokens, end = [], 0
    for match in pattern.finditer(text):
        if text[end : match.start()].strip(" \t\r\n,"):
            raise ReticleSvgError("invalid numeric or path syntax")
        tokens.append(match.group())
        end = match.end()
    if text[end:].strip(" \t\r\n,"):
        raise ReticleSvgError("invalid numeric or path syntax")
    return tokens


def arc_to_cubics(p0, rx, ry, phi, large, sweep, p1):
    if rx == 0 or ry == 0 or p0 == p1:
        return [("L", p0, p1)]
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (p0[0] - p1[0]) / 2, (p0[1] - p1[1]) / 2
    x1, y1 = cp * dx + sp * dy, -sp * dx + cp * dy
    rx, ry = abs(rx), abs(ry)
    lam = (x1 * x1) / (rx * rx) + (y1 * y1) / (ry * ry)
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
    den = rx * rx * y1 * y1 + ry * ry * x1 * x1
    co = math.sqrt(max(0.0, num / den)) * (-1 if large == sweep else 1)
    cx1, cy1 = co * rx * y1 / ry, -co * ry * x1 / rx
    cx = cp * cx1 - sp * cy1 + (p0[0] + p1[0]) / 2
    cy = sp * cx1 + cp * cy1 + (p0[1] + p1[1]) / 2

    def angle(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    t1 = angle(1, 0, (x1 - cx1) / rx, (y1 - cy1) / ry)
    dt = angle((x1 - cx1) / rx, (y1 - cy1) / ry, (-x1 - cx1) / rx, (-y1 - cy1) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    n = max(1, int(math.ceil(abs(dt) / (math.pi / 2) - 1e-9)))
    step = dt / n
    k = 4 / 3 * math.tan(step / 4)
    out, start = [], p0

    def point(t):
        x, y = rx * math.cos(t), ry * math.sin(t)
        return (cp * x - sp * y + cx, sp * x + cp * y + cy)

    def deriv(t):
        x, y = -rx * math.sin(t), ry * math.cos(t)
        return (cp * x - sp * y, sp * x + cp * y)

    for i in range(n):
        a, b = t1 + i * step, t1 + (i + 1) * step
        end = p1 if i == n - 1 else point(b)
        da, db = deriv(a), deriv(b)
        out.append(
            (
                "C",
                start,
                (start[0] + k * da[0], start[1] + k * da[1]),
                (end[0] - k * db[0], end[1] - k * db[1]),
                end,
            )
        )
        start = end
    return out


def parse_path(d):
    tokens = numeric_tokens(d or "", TOKEN)
    contours, segs = [], []
    i, cmd = 0, None
    cur = start = (0.0, 0.0)
    last_ctrl, last_cmd = None, None

    def num():
        nonlocal i
        if i >= len(tokens) or tokens[i].isalpha():
            raise ReticleSvgError(f"missing coordinates for path command {cmd}")
        v = finite_number(tokens[i])
        i += 1
        return v

    def close():
        nonlocal segs
        if segs:
            if segs[-1][-1] != start:
                segs.append(("L", segs[-1][-1], start))
            contours.append(segs)
        segs = []

    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
            if last_cmd is None and cmd not in "Mm":
                raise ReticleSvgError("a path must begin with M or m")
            if cmd in "Zz":
                close()
                cur = start
                last_cmd = cmd
                cmd = None
                continue
        elif cmd is None:
            raise ReticleSvgError("path data does not start with a command")
        rel = cmd.islower()
        c = cmd.upper()
        if last_cmd is None and c != "M":
            raise ReticleSvgError("a path must begin with M or m")
        ox, oy = cur if rel else (0.0, 0.0)
        if c == "M":
            close()
            cur = start = (ox + num(), oy + num())
            cmd = "l" if rel else "L"
            last_cmd = "M"
            continue
        if c == "L":
            p = (ox + num(), oy + num())
            segs.append(("L", cur, p))
        elif c == "H":
            p = ((cur[0] if rel else 0.0) + num(), cur[1])
            segs.append(("L", cur, p))
        elif c == "V":
            p = (cur[0], (cur[1] if rel else 0.0) + num())
            segs.append(("L", cur, p))
        elif c == "C":
            c0 = (ox + num(), oy + num())
            c1 = (ox + num(), oy + num())
            p = (ox + num(), oy + num())
            segs.append(("C", cur, c0, c1, p))
            last_ctrl = c1
        elif c == "S":
            c0 = (
                (2 * cur[0] - last_ctrl[0], 2 * cur[1] - last_ctrl[1])
                if last_cmd in ("C", "c", "S", "s")
                else cur
            )
            c1 = (ox + num(), oy + num())
            p = (ox + num(), oy + num())
            segs.append(("C", cur, c0, c1, p))
            last_ctrl = c1
        elif c == "Q":
            q = (ox + num(), oy + num())
            p = (ox + num(), oy + num())
            segs.append(("Q", cur, q, p))
            last_ctrl = q
        elif c == "T":
            q = (
                (2 * cur[0] - last_ctrl[0], 2 * cur[1] - last_ctrl[1])
                if last_cmd in ("Q", "q", "T", "t")
                else cur
            )
            p = (ox + num(), oy + num())
            segs.append(("Q", cur, q, p))
            last_ctrl = q
        elif c == "A":
            rx, ry, phi = num(), num(), math.radians(num())
            large, sweep = num(), num()
            if large not in (0, 1) or sweep not in (0, 1):
                raise ReticleSvgError("arc flags must be 0 or 1")
            p = (ox + num(), oy + num())
            segs.extend(arc_to_cubics(cur, rx, ry, phi, large, sweep, p))
        cur = p
        last_cmd = cmd
    close()
    return contours


def ellipse_contours(cx, cy, rx, ry):
    k = 4 / 3 * (math.sqrt(2) - 1)
    segs = []
    for j in range(4):
        a, b = j * math.pi / 2, (j + 1) * math.pi / 2
        pa = (cx + rx * math.cos(a), cy + ry * math.sin(a))
        pb = (cx + rx * math.cos(b), cy + ry * math.sin(b))
        da = (-rx * math.sin(a), ry * math.cos(a))
        db = (-rx * math.sin(b), ry * math.cos(b))
        segs.append(
            (
                "C",
                pa,
                (pa[0] + k * da[0], pa[1] + k * da[1]),
                (pb[0] - k * db[0], pb[1] - k * db[1]),
                pb,
            )
        )
    return [segs]


def element_contours(el):
    tag = el.tag.replace(SVG_NS, "")
    f = lambda k, d=0.0: finite_number(el.get(k, d))
    if tag == "line":
        return [[("L", (f("x1"), f("y1")), (f("x2"), f("y2")))]]
    if tag == "path":
        return parse_path(el.get("d"))
    if tag == "rect":
        x, y, w, h = f("x"), f("y"), f("width"), f("height")
        if el.get("rx") or el.get("ry"):
            raise ReticleSvgError("rounded rect: convert to path in Inkscape (Object to Path)")
        p = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        return [[("L", p[j], p[(j + 1) % 4]) for j in range(4)]]
    if tag == "circle":
        return ellipse_contours(f("cx"), f("cy"), f("r"), f("r"))
    if tag == "ellipse":
        return ellipse_contours(f("cx"), f("cy"), f("rx"), f("ry"))
    if tag == "polygon":
        v = [finite_number(x) for x in numeric_tokens(el.get("points", ""))]
        if len(v) < 6 or len(v) % 2:
            raise ReticleSvgError("a polygon needs at least three coordinate pairs")
        p = list(zip(v[0::2], v[1::2]))
        return [[("L", p[j], p[(j + 1) % len(p)]) for j in range(len(p))]]
    raise ReticleSvgError(f"unsupported element <{tag}>; convert it to a path")


SHAPE_TAGS = {"path", "rect", "circle", "ellipse", "polygon"}
IGNORED_TAGS = {"defs", "namedview", "metadata", "title", "desc", "style"}


def transform_contours(contours, m):
    return [[(s[0],) + tuple(apply(m, p) for p in s[1:]) for s in c] for c in contours]


class Shape:
    def __init__(self, role, colour, contours, source, mark=0, material="", material_name=""):
        self.material = material
        self.material_name = material_name
        self.role = role
        self.colour = colour
        self.contours = contours
        self.source = source
        self.mark = mark  # Mark Switch mark 1..15, 0 = shown with every mark


class SvgReticle:
    """What a reticle drawing holds: the filled shapes by role, the optional calibration line and centre mark,
    and every problem found, each naming the element and how to fix it."""

    def __init__(self, view_box):
        self.view_box = view_box
        self.shapes = []
        self.calibration = (
            None  # (length in user units, value, unit) from a "calibrate <value> <unit>" layer
        )
        self.centre = None  # (x, y) in user units from a "center" layer
        self.errors = []
        self.warnings = []


MARK = re.compile(r"^mark\s+(\d+)$")
MAX_MARK = 15
CALIBRATE = re.compile(r"^calibrat\w*\s+([-+]?(?:\d+\.?\d*|\.\d+))\s*(moa|mil|mils|mrad)$")


def element_name(el):
    label = el.get(INKSCAPE_NS + "label")
    name = el.get("id") or el.tag.rsplit("}", 1)[-1]
    return f"{name} ({label})" if label else name


def read_reticle_svg(path):
    """Reads a reticle drawing. Problems are collected in the result instead of stopping at the first one."""
    root = ET.fromstring(path) if isinstance(path, bytes) else ET.parse(path).getroot()
    if root.tag != SVG_NS + "svg":
        raise ReticleSvgError("not an SVG document")
    vb = [finite_number(x) for x in numeric_tokens(root.get("viewBox") or "")]
    if len(vb) != 4 or vb[2] <= 0 or vb[3] <= 0:
        raise ReticleSvgError(
            "the SVG needs a viewBox (Inkscape: Document Properties, set a viewBox)"
        )
    result = SvgReticle(tuple(vb))
    if any(el.tag == SVG_NS + "style" and (el.text or "").strip() for el in root.iter()):
        result.errors.append(
            "CSS stylesheets are not supported; apply styles directly to shapes or groups"
        )
    calibration_points, centre_points = [], []
    calibration_value = []

    inherited_keys = (
        "fill",
        "fill-opacity",
        "stroke",
        "stroke-opacity",
        "fill-rule",
        "visibility",
    )

    def walk(el, *args):
        try:
            walk_element(el, *args)
        except (ValueError, IndexError, OverflowError) as error:
            result.errors.append(f"{element_name(el)}: {error}")

    def walk_element(
        el,
        m,
        layer_role,
        special,
        inherited_opacity,
        mark=0,
        material=(),
        material_name=(),
        parent_style=None,
    ):
        tag = el.tag.replace(SVG_NS, "").split("}")[-1]
        if tag in IGNORED_TAGS:
            return
        style = {
            key: value for key, value in (parent_style or {}).items() if key in inherited_keys
        }
        for key, value in style_of(el).items():
            if value != "inherit":
                style[key] = value
        if style.get("display") == "none" or style.get("visibility") == "hidden":
            return
        opacity = inherited_opacity * opacity_value(style, "opacity")
        m = mat_mul(m, parse_transform(el.get("transform")))
        for key in ("mask", "clip-path", "filter"):
            if style.get(key, "none") != "none":
                raise ReticleSvgError(
                    f"uses a {key}; remove it, reticles are plain solid shapes"
                )
        if tag in ("g", "svg"):
            label = (el.get(INKSCAPE_NS + "label") or "").strip().lower()
            if el.get(INKSCAPE_NS + "groupmode") == "layer":
                if label in LAYER_ROLES:
                    layer_role = label
                    material, material_name = (), ()
                elif CALIBRATE.match(label):
                    v = CALIBRATE.match(label)
                    calibration_value.append(
                        (float(v.group(1)), "moa" if v.group(2) == "moa" else "mil")
                    )
                    special = "calibrate"
                elif label.startswith("calibrat"):
                    result.errors.append(
                        f"layer '{label}': name it like 'calibrate 10 MOA' or 'calibrate 2 mil'"
                    )
                    return
                elif label in ("center", "centre"):
                    special = "center"
                elif MARK.match(label):
                    number = int(MARK.match(label).group(1))
                    if not 1 <= number <= MAX_MARK:
                        result.errors.append(
                            f"layer '{label}': marks are numbered 1 to {MAX_MARK}"
                        )
                        return
                    if mark:
                        result.errors.append(
                            f"layer '{label}': a mark layer can not sit inside another mark layer"
                        )
                        return
                    mark = number
                elif label.startswith("mark"):
                    result.errors.append(f"layer '{label}': name it like 'mark 1'")
                    return
            if (
                tag == "g"
                and layer_role
                and label not in LAYER_ROLES
                and not MARK.match(label)
                and not special
            ):
                name = el.get(INKSCAPE_NS + "label") or el.get("id")
                if name:
                    material += (el.get("id") or name,)
                    material_name += (name,)
            for child in el:
                walk(
                    child, m, layer_role, special, opacity, mark, material, material_name, style
                )
            return
        if tag not in SHAPE_TAGS and not (tag == "line" and special == "calibrate"):
            result.errors.append(
                f"{element_name(el)}: <{tag}> is not supported; convert it with Path > Object to Path"
            )
            return
        try:
            contours = transform_contours(element_contours(el), m)
            if any(
                not math.isfinite(v) for c in contours for seg in c for p in seg[1:] for v in p
            ):
                raise ReticleSvgError("transformed coordinates must be finite")
        except ReticleSvgError as e:
            result.errors.append(f"{element_name(el)}: {e}")
            return
        if special == "calibrate":
            if any(seg[0] != "L" for c in contours for seg in c):
                raise ReticleSvgError("the calibration shape must be a straight line")
            calibration_points.append(
                [p for c in contours for seg in c for p in (seg[1], seg[-1])]
            )
            return
        if special == "center":
            centre_points.extend(
                p for c in contours for seg in c for p in segment_points(seg, 8)
            )
            return
        name = element_name(el)
        fill = style.get("fill", "#000000")
        if fill.startswith("url("):
            result.errors.append(f"{name}: uses a gradient or pattern fill; use a solid fill")
            return
        if style.get("stroke", "none") != "none":
            result.errors.append(
                f"{name}: has a stroke; use Path > Stroke to Path and give it a fill"
            )
            return
        if fill == "none":
            return
        if abs(opacity * opacity_value(style, "fill-opacity") - 1.0) > 1e-6:
            result.errors.append(f"{name}: is partly transparent; use full opacity")
            return
        if style.get("fill-rule", "nonzero") != "nonzero":
            result.errors.append(
                f"{name}: uses the even-odd fill rule; set Fill rule to nonzero in Fill and Stroke"
            )
            return
        try:
            colour = parse_colour(fill)
        except ReticleSvgError as e:
            result.errors.append(f"{name}: {e}")
            return
        if not layer_role:
            result.errors.append(
                f"{name}: no role layer; move this shape into an Inkscape layer named paint or illum (legacy ink is also accepted)"
            )
            return
        role = layer_role
        if contours:
            result.shapes.append(
                Shape(
                    role,
                    colour,
                    contours,
                    el.get("id"),
                    mark,
                    material,
                    " / ".join(material_name),
                )
            )

    walk(root, IDENTITY, None, None, 1.0)
    if not result.shapes and not result.errors:
        result.errors.append(
            "The SVG has no visible reticle shapes. Add filled shapes to a paint or illum layer."
        )

    if calibration_points:
        if len(calibration_points) != 1 or len(calibration_value) != 1:
            result.errors.append("the calibrate layer must hold exactly one straight line")
        else:
            pts = calibration_points[0]
            a, b = max(
                ((a, b) for a in pts for b in pts),
                key=lambda pair: math.dist(*pair),
                default=((0, 0), (0, 0)),
            )
            length = math.dist(a, b)
            straight = length > 0 and all(
                abs((p[0] - a[0]) * (b[1] - a[1]) - (p[1] - a[1]) * (b[0] - a[0]))
                <= 1e-9 * length * length
                for p in pts
            )
            if not straight or calibration_value[0][0] <= 0:
                result.errors.append(
                    "the calibrate layer needs one straight line with positive length and scale"
                )
            else:
                result.calibration = (length, calibration_value[0][0], calibration_value[0][1])
    if centre_points:
        xs, ys = [p[0] for p in centre_points], [p[1] for p in centre_points]
        result.centre = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
    return result


# --- geometry helpers -------------------------------------------------------------------------


def segment_points(seg, n=8):
    kind = seg[0]
    if kind == "L":
        return [seg[1], seg[2]]
    pts = []
    for j in range(n + 1):
        t = j / n
        if kind == "Q":
            p0, c, p1 = seg[1:]
            u = 1 - t
            pts.append(
                (
                    u * u * p0[0] + 2 * u * t * c[0] + t * t * p1[0],
                    u * u * p0[1] + 2 * u * t * c[1] + t * t * p1[1],
                )
            )
        else:
            p0, c0, c1, p1 = seg[1:]
            u = 1 - t
            pts.append(
                (
                    u**3 * p0[0] + 3 * u * u * t * c0[0] + 3 * u * t * t * c1[0] + t**3 * p1[0],
                    u**3 * p0[1] + 3 * u * u * t * c0[1] + 3 * u * t * t * c1[1] + t**3 * p1[1],
                )
            )
    return pts


def contour_polygon(contour, n=16):
    pts = []
    for seg in contour:
        sp = segment_points(seg, n)
        pts.extend(sp[:-1])
    return pts


def bounds(contours):
    xs, ys = [], []
    for c in contours:
        for p in contour_polygon(c, 8):
            xs.append(p[0])
            ys.append(p[1])
    return min(xs), min(ys), max(xs), max(ys)
