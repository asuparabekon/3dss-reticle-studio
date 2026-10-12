"""Build the D3D11 preview host and copy the exact shader sources it runs."""

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def build(engine=None, output=None, patch=None):
    output = Path(output or ROOT / ".preview")
    output.mkdir(parents=True, exist_ok=True)
    shaders = output / "shaders"
    shaders.mkdir(exist_ok=True)
    shutil.copy2(ROOT / "native/check.png", shaders / "check.png")
    patch = Path(patch) / "gamedata/shaders/r3" if patch else ROOT / "native/shaders"
    engine = Path(engine) / "gamedata/shaders/r3" if engine else ROOT / "native/shaders"
    files = [
        patch / name
        for name in (
            "reticle_msdf.h",
            "reticle_msdf_reflex.h",
            "reticle_msdf_speckle.h",
            "reticle_msdf_states.h",
        )
    ]
    files += [
        engine / name
        for name in (
            "reticle_glow.h",
            "reticle_glow_down.ps",
            "reticle_glow_blur.ps",
            "reticle_emission_combine.ps",
        )
    ]
    files += [ROOT / "native" / name for name in ("common.h", "hdr10.h", "preview_reticle.ps")]
    # Provenance is recorded only for an SVG/DDS pair whose serialized export
    # was validated. Rebuilding the host must not bless edited SVGs as matching.
    shutil.copy2(ROOT / "reticles/preview_sources.json", shaders / "examples.json")
    manifest = {
        "examples.json": hashlib.sha256((shaders / "examples.json").read_bytes()).hexdigest()
    }
    for source in files:
        if not source.is_file():
            raise FileNotFoundError(f"Required preview shader is missing: {source}")
        shutil.copy2(source, shaders / source.name)
        manifest[source.name] = hashlib.sha256(source.read_bytes()).hexdigest()
    (shaders / "manifest.json").write_text(json.dumps(manifest, indent=2))
    kit = Path("C:/Program Files (x86)/Windows Kits/10/bin")
    compilers = sorted(kit.glob("*/x64/fxc.exe"))
    if not compilers:
        raise FileNotFoundError(
            "Install the Windows SDK to build the native preview (fxc.exe is missing)"
        )
    fxc = compilers[-1]
    for name in (
        "preview_reticle.ps",
        "reticle_glow_down.ps",
        "reticle_glow_blur.ps",
        "reticle_emission_combine.ps",
    ):
        subprocess.run(
            [
                str(fxc),
                "/nologo",
                "/T",
                "ps_5_0",
                "/E",
                "main",
                "/Zpr",
                "/I",
                str(shaders),
                "/Fo",
                str(shaders / (name + ".cso")),
                str(shaders / name),
            ],
            check=True,
        )
    installer = Path("C:/Program Files (x86)/Microsoft Visual Studio/Installer/vswhere.exe")
    vs = subprocess.check_output(
        [
            str(installer),
            "-latest",
            "-products",
            "*",
            "-requires",
            "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
            "-property",
            "installationPath",
        ],
        text=True,
    ).strip()
    environment = Path(vs) / "VC/Auxiliary/Build/vcvars64.bat"
    command = output / "build.cmd"
    command.write_text(
        '@echo off\ncall "' + str(environment) + '" >nul\n'
        "if errorlevel 1 exit /b 1\n"
        'cl /nologo /std:c++17 /EHsc /O2 /MT "'
        + str(ROOT / "native/reticle_preview.cpp")
        + '" '
        '/Fo"'
        + str(output / "reticle_preview.obj")
        + '" /Fe"'
        + str(output / "reticle_preview.exe")
        + '" '
        "/link d3d11.lib d3dcompiler.lib\nexit /b %errorlevel%\n"
    )
    subprocess.run(["cmd", "/c", str(command)], check=True)
    manifest["reticle_preview.cpp"] = hashlib.sha256(
        (ROOT / "native/reticle_preview.cpp").read_bytes()
    ).hexdigest()
    for name in (
        "preview_reticle.ps",
        "reticle_glow_down.ps",
        "reticle_glow_blur.ps",
        "reticle_emission_combine.ps",
    ):
        manifest[name + ".cso"] = hashlib.sha256(
            (shaders / (name + ".cso")).read_bytes()
        ).hexdigest()
    (shaders / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", help="Optional engine checkout to refresh shader snapshots")
    parser.add_argument(
        "--patch", help="Optional GAMMA Patch checkout to refresh MSDF snapshots"
    )
    parser.add_argument("--out")
    args = parser.parse_args()
    build(args.engine, args.out, args.patch)
