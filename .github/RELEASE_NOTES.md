Standalone Windows editor for MSDF reticles used by 3D Shader Scopes.

Download **the attached ZIP**, extract the entire folder, and run **MSDFReticleStudio.exe**. The executable retains its existing name. No Python or Visual Studio installation is required. Keep the folder and its supporting files together.

Open a layered SVG or choose the bundled Cortex/EOTech examples. Edit named paint and illumination groups, select Mark Switch states, preview glow and holographic noise, and export a validated DDS. The authoring guide is included under `source/AUTHORING.md`.

**Do not install Studio through MO2.** Only the exported reticle's `gamedata` folder belongs in a game mod. Exported textures require the MSDF-aware GAMMA patch shaders; exposure-aware glow also needs the matching engine feature.

The D3D11 preview uses runtime shader implementations with controlled SDR inputs. Day/night adaptation uses synthetic scene luminance. Exact live-scene pixel parity and HDR output are not verified.


SHA256SUMS.txt contains the checksum of the release archive.
