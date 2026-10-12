// Offscreen host declarations used by the unchanged engine glow passes.
struct p_screen { float4 hpos : SV_Position; float2 tc0 : TEXCOORD0; };
SamplerState smp_rtlinear;
Texture2D s_tonemap;
