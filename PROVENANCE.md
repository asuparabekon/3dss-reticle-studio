# Source provenance

3DSS Reticle Studio contains the standalone reticle authoring tool, native D3D11 preview and example artwork.

## Runtime shader sources

MSDF shader snapshots originate from the [GAMMA compatibility patch](https://github.com/asuparabekon/xray-monolith-bodycam-gamma-compatibility-patch). Glow shaders originate from the Bodycam X-Ray engine's 3DSS implementation. Native wrapper headers and the preview host belong to this tool. Builds record the sources and compiled bytecode in `.preview/shaders/manifest.json`.

3D Shader Scopes: LVutner and party_50. GAMMA integration: Redotix99, Andtheherois, party50 and MsPizza727. Authentic Reticle for 3DSS: Napolemon. Bodycam integration and Studio: asuparabekon. Original code and artwork retain their authorship and terms; this repository does not assign a new licence to inherited content.

## Examples

Cortex and EOTech examples include layered SVGs, settings and validated DDS caches. Named groups support independently editable materials. The original lowercase examples retain their legacy formats; Cortex.svg and EOTech.svg use material states. Source/DDS identities are recorded in `reticles/preview_sources.json`.

Current example atlas hashes:

| File | SHA256 |
| --- | --- |
| `native/example_textures/Cortex.dds` | `3e7c470df5dfacc14c05308fbb0130b8b9820b45eb1f431f7428b213e201475b` |
| `native/example_textures/EOTech.dds` | `e2153c787a8a0b921ddc5d285e72e54d9f2841e46b894088efa730b64eb95a88` |
| `native/example_textures/material_states.dds` | `7dcc51b221d16ed189149a7c265c7aae5fafe264e077254a4d8af119c1d48d56` |
| `native/example_textures/scope_reticles/cortex/ebr9/cortex.dds` | `18e71b5b0832bc681f57b80bbe0571c909bb352557384b4f7e66ec8c7f931c03` |
| `native/example_textures/wpn_addon_scope_eotech_crosshair_dot.dds` | `24072f8aa79bbd377ee05563b0a24ef09f7aa281f48dfbb33e23da4921b3722c` |

## Dependencies

msdfgen retains its MIT licence in `third_party/msdfgen-LICENSE.txt`. Qt notices are in `third_party/qt/`. Portable builds retain notices supplied by Python dependencies in their `licenses/` folder.
