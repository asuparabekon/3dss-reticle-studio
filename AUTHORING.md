# Authoring 3DSS reticles

Studio converts filled SVG shapes into one DDS containing geometry, material colours, Mark Switch states and effect settings. Players do not need Studio, the SVG or companion settings.

## Open a drawing

Run `MSDFReticleStudio.exe` from its extracted portable folder. Use **Open SVG**, drag a drawing into the window, or select **File > Examples > Cortex / EOTech**.

An SVG alone is sufficient. Companion settings are imported if present, but the GUI exposes settings without an INI editor. Saving a project or exporting leaves the original SVG intact. External SVG edits automatically rebuild the preview.

**Save Project** stores the drawing and settings in one portable `.reticle` file.

## SVG layers

In Inkscape, put every visible shape in a named role layer:

| Layer | Meaning |
| --- | --- |
| `paint` | Non-emitting surface, using its SVG fill, including black. |
| `illum` | Emitted light, using its SVG fill as the authored light colour. Disappears when off. |
| `ink` | Legacy alias for black paint. Prefer `paint` for new drawings. |

Colours do not assign roles. Blue paint remains blue paint. To keep a coloured surface visible with its light off, add paint beneath the corresponding illumination shape.

Use opaque solid fills. Convert strokes with **Path > Stroke to Path** and text with **Path > Object to Path**. Gradients, blur, masks, clipping and transparency are unsupported. Unassigned shapes block conversion.

Named groups make individual parts editable:

```text
Mark 1
  paint
    Outer scale
    Centre ring
  illum
    Ring
    Centre dot
Mark 2
  paint
    Alternate scale
  illum
    Crosshair
```

Choose your own group names; Studio discovers them automatically. Labels supply displayed names; SVG IDs preserve settings across renames. **SVG fill** preserves each shape's fill, while **Custom colour** recolours the whole group. Ungrouped shapes in a role layer form its default material.

Groups outside numbered Mark layers are shared. Keep intended layers visible. Deleting a group with saved overrides requires resolving those overrides before export.

## Patterns and Mark Switch states

A **pattern** selects geometry. A **state** selects a pattern and material settings. Multiple states can reuse one pattern without duplicating its atlas geometry.

For example, State 1 can show Pattern 1 with green paint and no light. State 2 can reuse it with green paint and red illumination. State 3 can change only the centre dot to blue if that dot has its own group.

Each material has a **Visible** toggle that preserves its settings when disabled. Illumination also has a brightness slider and exact numeric field; zero emits no light. The selected state drives both editor and preview.

The format supports **15 states**, **16 materials** and **16 atlas tiles**. Detailed materials can consume several tiles and reach the tile limit first. Studio does not edit 3DSS configuration: the sight must expose enough Mark Switch selections.

## Illumination source

Under **Advanced > Illumination source**:

- **This reticle** uses authored material colours and state brightness. Mark Switch still supplies selection and dot offset, but its RGB/level does not recolour the light.
- **Game settings (3DSS)** uses the game's Mark Switch colour and light level. State brightness still applies; black means off.

Legacy projects retain their earlier behaviour. Paint and emitted light are independent, so green paint can sit beneath red illumination.

## Calibration

Uncalibrated drawings start at **1 SVG unit per MOA**. This is an editable starting value, not a measured sight dimension. Change it in Advanced or add a layer named `calibrate 10 MOA` containing one straight line of that angular length. Other values and `mil` units work too. Calibration lines are not rendered.

Use a `center` layer with a small marker to specify the optical centre. Otherwise, the converter derives it from the central illuminated dot or page centre.

**3DSS scale** follows the replaced bitmap's mapping, including zoom and focal-plane behaviour. **True-angle scale** uses angular dimensions. Maximum zoom helps size the texture; it does not force game magnification.

## Appearance and preview

Appearance holds material colour, brightness and glow. Holographic effects reveal grain controls. Hover labels for help or focus them with Tab.

- Wheel over the image to zoom toward the pointer; left/middle drag pans.
- Double-click or press **F** to fit; **1** selects physical-pixel inspection.
- Wheel over controls scrolls the panel without changing values.
- **Animate** advances holographic noise using elapsed time.
- **View > Reduce motion** disables interface fades and smooth zoom.
- **Preview options > Scene settings** controls preview resolution, exposure and player settings. Read-only `user.ltx` import can match exposure/glow settings.

Eye adaptation runs independently of holographic animation. The D3D11 preview uses the runtime MSDF/reflex and glow shaders with controlled clear-glass SDR inputs. It does not reconstruct live weather, lens dirt, thermal state or weapon optics. Exact live-frame parity and HDR output are unverified.

Appearance edits can update metadata without rebuilding geometry. SVG and quality changes need a build. Complex drawings can take minutes; Build details shows progress and errors. DDS size includes mipmaps and becomes exact after compilation.

## Export and install

1. Set the target texture path in Export, such as `wpn\scope_reticles\example\reticle`, without `.dds`.
2. Choose an output folder and select **Export DDS**.
3. Studio reopens the DDS and checks every pattern before replacing output. Failures preserve the previous DDS.
4. Put the generated `gamedata` in a mod. For MO2, ZIP it with `gamedata/` directly at the archive root.
5. Let the mod win the original reticle's texture conflict. Restart the game after replacement.

The game needs the [MSDF-aware GAMMA patch shaders](https://github.com/asuparabekon/xray-monolith-bodycam-gamma-compatibility-patch); exposure-aware glow needs the matching engine feature. PiP may be disabled. Studio itself does not belong in MO2.

Automatic compression selects BC5 when packing and quality allow it, otherwise RGBA8. Higher quality cannot fix unsupported SVG content or incorrect calibration.

## Examples

`reticles/Cortex.svg` has named paint/illumination groups and states sharing one pattern. `reticles/EOTech.svg` has six states using five patterns, including red/green states sharing geometry. Keep companion settings beside them to retain their scale, effects and texture paths, or save a project.

The validated Cortex atlas uses 42.67 MiB RGBA8; EOTech uses 2.67 MiB BC5. Matching source/DDS caches allow immediate previews. These examples do not change installed game textures until exported and installed.

## Command-line conversion

Install `requirements.txt`, then run:

```powershell
python make_reticle.py my_reticle.svg
python make_reticle.py my_reticle.svg --out output
```

Unlike the GUI, the CLI creates a `.reticle.ini` template and stops if no companion settings exist. Fill in texture path, calibration and appearance, then rerun. `make_reticle.bat` sets up its environment and downloads msdfgen on first use.

Output includes the DDS under `gamedata/textures`, `report.txt` and approximate CPU-rendered PNG previews. The report identifies element IDs, packing decisions and quality failures. `--no-gate` produces an unverified draft; Studio's export always validates.

For native and portable build instructions, see [README.md](README.md).
