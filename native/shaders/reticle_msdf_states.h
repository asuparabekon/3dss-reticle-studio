// Formats 5/6 separate switch states from pattern geometry. Older atlases keep
// their original mark selection and illumination behaviour.
#ifndef RETICLE_MSDF_STATES_H
#define RETICLE_MSDF_STATES_H
struct ReticleState
{
    uint pattern;
    uint flags; // bit 0: override the illuminated SVG fills with this state's colour
    float3 colour;
    float brightness;
    uint hidden_materials; // formats 7/8: one bit per hidden material
};

ReticleState reticle_state(Texture2D t, ReticleAtlas a, float current)
{
    ReticleState state = (ReticleState)0;
    state.pattern = uint(max(current, 0.0)) % max(a.mark_count, 1u) + 1;
    state.brightness = 1.0;
    if (a.state_count > 0)
    {
        const uint index = uint(max(current, 0.0)) % a.state_count;
        const uint k = 12 + 2 * index; // byte 48, two aligned header texels per state
        const uint4 first = rmsdf_texel(t, a.packed, k % a.desc_width, 1 + k / a.desc_width);
        const uint4 second = rmsdf_texel(t, a.packed, (k + 1) % a.desc_width, 1 + (k + 1) / a.desc_width);
        state.pattern = first.x;
        state.flags = first.y;
        state.colour = float3(first.zw, second.x) / 255.0;
        state.brightness = second.y / 255.0;
        if (a.material_count > 0) state.hidden_materials = second.z | (second.w << 8);
    }
    return state;
}
// Formats 7/8 share geometry between states and store one RGBA value per material.
// Alpha's high bit enables the RGB override; its remaining bits carry brightness.
float4 reticle_material(Texture2D t, ReticleAtlas a, float current, uint material, float3 svg_colour)
{
    if (a.material_count == 0) return float4(svg_colour, 1.0);
    const uint state = uint(max(current, 0.0)) % a.state_count;
    const uint k = 46 + 16 * state + material;
    const uint4 value = rmsdf_texel(t, a.packed, k % a.desc_width, 1 + k / a.desc_width);
    return float4((value.w & 128) != 0 ? float3(value.rgb) / 255.0 : svg_colour,
        float(value.w & 127) / 127.0);
}
#endif
