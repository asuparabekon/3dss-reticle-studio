"""msdfgen, the official prebuilt Windows binary (MIT, Viktor Chlumsky, third_party/msdfgen-LICENSE.txt).

It is downloaded on first use into .msdfgen beside this module and checked against a pinned hash; nothing is
compiled. Only used at build time, never shipped with a reticle.
"""

import hashlib
import io
import os
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import numpy as np

VERSION = "1.13"
URL = f"https://github.com/Chlumsky/msdfgen/releases/download/v{VERSION}/msdfgen-{VERSION}-win64.zip"
SHA256 = "08615362a7e4dc822fe1f4ef6a33ae62d7da0e3b91b6334a2ad2930bf4092b0c"
HOME = Path(__file__).resolve().parent / ".msdfgen"


class MsdfgenError(RuntimeError):
    pass


def executable():
    exe = HOME / f"msdfgen-{VERSION}" / "msdfgen.exe"
    if exe.exists():
        return exe
    try:
        data = urllib.request.urlopen(URL, timeout=60).read()
    except OSError as e:
        raise MsdfgenError(
            f"could not download msdfgen from {URL}: {e}. Download it by hand and unzip "
            f"msdfgen.exe into {exe.parent}"
        ) from e
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise MsdfgenError(f"the msdfgen download from {URL} does not match the expected file")
    exe.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".msdfgen-", dir=exe.parent)
    try:
        with os.fdopen(fd, "wb") as output, zipfile.ZipFile(io.BytesIO(data)) as z:
            output.write(z.read("msdfgen/msdfgen.exe"))
        os.replace(temporary, exe)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return exe


def mtsdf(shape_file, size, distance_range, work):
    """MTSDF of a shape description in tile-normalised coordinates (y up), size x size texels, rows top to
    bottom. Channels: median distance RGB, true distance A; 0.5 is the edge."""
    out = Path(work) / "tile.bin"
    cmd = [
        str(executable()),
        "mtsdf",
        "-shapedesc",
        str(shape_file),
        "-dimensions",
        str(size),
        str(size),
        "-scale",
        str(size),
        "-translate",
        "0",
        "0",
        "-range",
        repr(distance_range),
        "-format",
        "binfloat",
        "-o",
        str(out),
        "-scanline",
    ]
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired as error:
        raise MsdfgenError("msdfgen did not finish this tile within five minutes") from error
    if r.returncode != 0 or not out.exists():
        raise MsdfgenError(f"msdfgen failed: {(r.stdout + r.stderr).strip()}")
    data = np.fromfile(out, np.float32)
    os.remove(out)
    return data.reshape(size, size, 4)[::-1]
