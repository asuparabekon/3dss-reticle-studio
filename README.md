# 3DSS Reticle Studio

A Windows SVG-to-DDS editor for **3D Shader Scopes (3DSS)** in S.T.A.L.K.E.R. Anomaly. Open a layered SVG, adjust paint, illumination and holographic effects, preview the result, and export a self-describing reticle atlas.

## Getting started

1. Download the portable Windows ZIP from [Releases](https://github.com/asuparabekon/3dss-reticle-studio/releases).
2. Extract the whole folder and run `MSDFReticleStudio.exe`. Python and Visual Studio are not required.
3. Open **File > Examples > Cortex / EOTech**, or drop your own SVG into the window.
4. Edit the selected Mark Switch state's materials, then use **Export DDS**.

The executable and window currently retain the name **MSDF Reticle Studio**. This repository distributes that application as **3DSS Reticle Studio**.

Read the [authoring guide](AUTHORING.md) for layers, calibration, materials, switching and installing exported reticles.

## Compatibility

Exported DDS files require the MSDF-aware shaders supplied by the [GAMMA compatibility patch](https://github.com/asuparabekon/xray-monolith-bodycam-gamma-compatibility-patch). Exposure-aware glow also needs the matching engine feature. MSDF works with PiP disabled. Studio does not change weapon configuration, magnification or Mark Switch selection counts.

Studio is a standalone desktop application: **do not install its ZIP through MO2**. Only exported `gamedata` belongs in a game mod.

## Features

- Named paint and illumination materials discovered from SVG groups.
- Up to 15 states reusing patterns with independent colours, brightness and visibility.
- Holographic grain, animated noise, glow and exposure controls.
- D3D11 preview using runtime reticle and glow shader implementations.
- Pointer-centred zoom, drag to pan, live SVG reload and portable `.reticle` projects.
- DDS size estimates and validated exports that preserve previous output on failure.

## Preview limits

Day/night uses synthetic luminance with the engine's adaptation equations. The preview does not reconstruct live weather, weapon optics, lens dirt or thermal state. Exact live-game pixel parity and HDR output are unverified. CLI PNG previews are approximations; the desktop preview uses D3D11.

Very fine or uncalibrated drawings can fail export checks even when a draft is visible. Correct the drawing, calibration or quality settings before exporting.

## Build from source

Use Python 3.11 or newer on Windows, Visual Studio C++ Build Tools and the Windows SDK:

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python build_native.py
.venv/Scripts/python author_reticle.py
.venv/Scripts/python build_studio.py --out dist
```

The native build uses snapshots in `native/shaders`; no game installation or sibling checkout is required. Optional `--engine` and `--patch` arguments select other shader sources. Builds generate source/bytecode hashes under `.preview/shaders`. The first atlas build downloads msdfgen.

## Source layout

| Area | Modules |
| --- | --- |
| Window and controls | `author_reticle.py`, `authoring_controls.py`, `authoring_states.py`, `authoring_canvas.py` |
| Documents and builds | `authoring_document.py`, `authoring_build.py`, `authoring_jobs.py` |
| Preview and exposure | `reticle_preview.py`, `preview_process.py`, `authoring_adaptation.py`, `native/` |
| Conversion | `svg_reticle.py`, `reticle_build.py`, `block_compress.py` |
| Format and validation | `reticle_format.py`, `reticle_dds.py`, `gate_reticle.py` |
| Patterns and materials | `reticle_states.py`, `reticle_materials.py` |

See [PROVENANCE.md](PROVENANCE.md) and `third_party/` for origins and notices. Inherited code and artwork retain their original terms.

## Automated builds and releases

Pushes to `main` build and validate a downloadable `release-package` artifact in GitHub Actions. They do not publish a release. The workflow can also be run manually for a build.

To publish, update `VERSION`, push the source, then push a matching version tag such as `v1.2.3`. Tags must match the source version. The workflow builds the ZIP, checks its contents, uploads it with `SHA256SUMS.txt`, and publishes the release only after the build succeeds. Use a new version for each release; existing releases are not overwritten.

The Windows build compiles the native preview and portable app and checks packaged shader hashes. Hosted CI does not validate live-game visuals.
