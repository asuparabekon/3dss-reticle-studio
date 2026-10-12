// MSDF reticles on reflex and holographic sights (models_reflex_reticle*.ps). The reticle is collimated: its
// angle is the direction from the viewpoint (the view's origin, also a PiP objective that sees the sight)
// through the glass, measured against the glass normal, so it sits at infinity like the bitmap path; in 3dss scale mode it follows the bitmap's own coordinate instead. Reflex output
// is light only (additive blend), no ink. While the engine holds the reticle glow targets at slots
// 2 and 3 (reticle_msdf_reflex_glow.x) the light goes there, out of bloom.
//
// Holographic sights draw with the look their atlas authors (ReticleHologram): softer and dimmer, laser speckle
// fixed in reticle angle that moves with the eye's position relative to the sight, and optionally smoothly
// evolving noise, a ghost image, colour fringes and low-step flicker. r__reticle_msdf_holo_level and _speckle
// scale level and noise depth on top.
#ifndef RETICLE_MSDF_REFLEX_H
#define RETICLE_MSDF_REFLEX_H

#include "reticle_msdf.h"

#ifndef RETICLE_HOLO_EYE_DECLARED
#define RETICLE_HOLO_EYE_DECLARED
uniform float4 reticle_holo_eye; // engine: the physical eye in the current view space, w = 1 when set
#endif

// A reflex material's outputs: the scene (SV_Target0) and the reticle glow core and glow source (SV_Target2, 3).
// With the reflex blend an output with alpha 0 leaves its target as it was.
struct ReflexReticleOut
{
	float4 colour : SV_Target0;
	float4 glow : SV_Target2;
	float4 glow_source : SV_Target3;
};

// A reflex material's outputs: the bitmap, or the MSDF light into the glow target while it is bound, else into
// the scene. The material blends by source alpha onto what is there.
ReflexReticleOut reticle_msdf_reflex_out(ReticleAtlas atlas, float4 bitmap, float3 light)
{
	ReflexReticleOut o;
	o.colour = bitmap;
	o.glow = 0;
	o.glow_source = 0;
	if (atlas.recognised)
	{
		const bool glow = reticle_msdf_reflex_glow.x > 0.5 && reticle_msdf_emission_external();
		if (reticle_msdf_reflex_glow.x < -0.5)
			light = 0;
		o.colour = glow ? 0 : float4(reticle_msdf_inline(light), 1);
		o.glow = glow ? reticle_msdf_core_out(light) : 0;
		o.glow_source = glow ? reticle_msdf_glow_out(atlas, light) : 0;
	}
	return o;
}

// A hologram's look, from the atlas (format 3) or the format 2 defaults: speckle depth, grain (units) and grains
// of shift per mm of eye movement; animated noise depth, changes per second and grain; edge softness; level
// against an LED reticle; ghost level and offset (units); colour fringe (units); low-step flicker depth.
struct ReticleHologram
{
	float speckle, speckle_size, speckle_eye_shift;
	float noise, noise_speed, noise_size;
	float softness, level;
	float ghost_level;
	float2 ghost_offset;
	float fringe, flicker;
};

ReticleHologram reticle_hologram(Texture2D t, ReticleAtlas a)
{
	ReticleHologram h;
	h.speckle = 0.3; h.speckle_size = 0.35; h.speckle_eye_shift = 0.23;
	h.noise = 0.0; h.noise_speed = 1.0; h.noise_size = 1.0;
	h.softness = 1.25; h.level = 0.75;
	h.ghost_level = 0.0; h.ghost_offset = 0.0;
	h.fringe = 0.0; h.flicker = 0.0;
	if (a.desc_base >= 40)
	{
		h.speckle = rmsdf_byte(t, a, 24) / 255.0;
		h.speckle_size = max(rmsdf_byte(t, a, 25) / 100.0, 0.01);
		h.speckle_eye_shift = rmsdf_byte(t, a, 26) / 100.0;
		h.noise = rmsdf_byte(t, a, 27) / 255.0;
		h.noise_speed = rmsdf_byte(t, a, 28) / 16.0;
		h.noise_size = max(rmsdf_byte(t, a, 29) / 100.0, 0.01);
		h.softness = max(rmsdf_byte(t, a, 30) / 32.0, 1.0);
		h.level = rmsdf_byte(t, a, 31) / 100.0;
		h.ghost_level = rmsdf_byte(t, a, 32) / 255.0;
		const uint gx = rmsdf_byte(t, a, 33), gy = rmsdf_byte(t, a, 34);
		h.ghost_offset = float2(gx >= 128 ? float(gx) - 256.0 : float(gx), gy >= 128 ? float(gy) - 256.0 : float(gy)) / 8.0;
		h.fringe = rmsdf_byte(t, a, 35) / 100.0;
		h.flicker = rmsdf_byte(t, a, 36) / 255.0;
	}
	return h;
}

float rmsdf_hash(float2 p)
{
	return frac(sin(dot(p, float2(127.1, 311.7))) * 43758.5453);
}

float rmsdf_value_noise(float2 p)
{
	const float2 i = floor(p);
	const float2 f = p - i;
	const float2 u = f * f * (3.0 - 2.0 * f);
	return lerp(lerp(rmsdf_hash(i), rmsdf_hash(i + float2(1, 0)), u.x),
		lerp(rmsdf_hash(i + float2(0, 1)), rmsdf_hash(i + float2(1, 1)), u.x), u.y);
}

#include "reticle_msdf_speckle.h"

// view-space position, tangent frame and UV of the glass fragment; dot_offset = the mark switch zero offset
// in bitmap UV (0.16 rad per unit, the legacy reticle scale); legacy_tc = where the material's bitmap path would
// sample (in the Mark Switch cell), for 3dss scale mode. Returns the light in scene luminance.
float3 reticle_msdf_reflex(Texture2D t, SamplerState smp, ReticleAtlas atlas, float3 P, float3 T, float3 B, float3 N,
	float2 tc0, float2 dot_offset, float2 legacy_tc)
{
	const float3 eye = reticle_holo_eye.w > 0.5 ? reticle_holo_eye.xyz : float3(0, 0, 0);
	const float3 n = normalize(N);
	const float3 tt = normalize(T - n * dot(T, n));
	const float3 bb = normalize(B - n * dot(B, n));
	const float depth = max(abs(dot(P, n)), 1e-5);
	const float units = rmsdf_units_per_radian(atlas);
	float2 angle = float2(dot(P, tt), dot(P, bb)) / depth * units + dot_offset * 0.16 * units;
	if (reticle_msdf_legacy_scale(atlas))
		angle = reticle_msdf_legacy_angle(atlas, legacy_tc);
	// r__reticle_msdf 0 compares against the plain coverage look: none of the hologram's effects
	const bool holo = (atlas.flags & RMSDF_FLAG_HOLO) != 0 && !reticle_msdf_legacy() && atlas.valid;
	const ReticleHologram h = reticle_hologram(t, atlas);
	rmsdf_soften = holo ? h.softness : 1.0;
	const float2 jx = ddx(angle);
	const float2 jy = ddy(angle);

	// the sight's centre from the glass UV gradient, for the eye position relative to the sight
	const float2 tc_dx = ddx(tc0), tc_dy = ddy(tc0);
	const float3 p_dx = ddx(P), p_dy = ddy(P);
	const float det = tc_dx.x * tc_dy.y - tc_dy.x * tc_dx.y;
	const float inv = abs(det) > 1e-12 ? 1.0 / det : 0.0;
	const float3 dp_du = (p_dx * tc_dy.y - p_dy * tc_dx.y) * inv;
	const float3 dp_dv = (p_dy * tc_dx.x - p_dx * tc_dy.x) * inv;
	const float3 centre = P + dp_du * (0.5 - tc0.x) + dp_dv * (0.5 - tc0.y);
	const float2 eye_mm = float2(dot(eye - centre, tt), dot(eye - centre, bb)) * 1000.0;

	if (!atlas.valid)
		return (atlas.recognised && reticle_msdf_control.y > 0.5) ? float3(0.25, 0, 0.25) : 0;

	// black is off; a sight with no Mark Switch colours needs an atlas with its own mark colours
	const float4 illumination = reticle_msdf_lighting(t, atlas, markswitch_color, markswitch_current.x);
	// The marks at up to four reticle angles: the image (k 0), a hologram's red and blue colour fringes, the red
	// and blue images a little apart (1, 2), and its ghost image from an internal reflection (3). One loop, so the
	// marks are compiled once instead of four times.
	const bool fringe = holo && h.fringe > 0.0;
	const bool ghost = holo && h.ghost_level > 0.0;
	const float2 fringe_shift = float2(0.5 * h.fringe, 0.0);
	float3 light = 0;
	[loop] for (uint k = 0; k < 4; ++k)
	{
		if ((k == 1 || k == 2) && !fringe)
			continue;
		if (k == 3 && !ghost)
			continue;
		const float2 offset = k == 0 ? 0.0 : (k == 1 ? fringe_shift : (k == 2 ? -fringe_shift : -h.ghost_offset));
		const float3 e = reticle_msdf_emission(atlas, reticle_msdf_marks(t, smp, atlas, angle + offset, jx, jy,
			markswitch_current.x), illumination);
		if (k == 0)
			light = e;
		else if (k == 1)
			light.r = e.r;
		else if (k == 2)
			light.b = e.b;
		else
			light += h.ghost_level * e;
	}
	if (holo)
	{
		const bool set = reticle_msdf_control.w > 0.5;
		const float level = h.level * (set ? max(reticle_msdf_params.x, 0.0) : 1.0);
		const float depth = set ? max(reticle_msdf_params.z, 0.0) : 1.0;
		float grain = 1.0;
		if (h.speckle > 0.0)
		{
			const float n = rmsdf_speckle(angle / h.speckle_size + eye_mm * h.speckle_eye_shift,
				jx / h.speckle_size, jy / h.speckle_size);
			grain *= lerp(1.0, n, saturate(h.speckle * depth));
		}
		if (h.noise > 0.0)
		{
			// value noise that blends from one random field to the next, noise_speed times a second
			const float time = timers.x * h.noise_speed;
			const float k = floor(time);
			const float2 q = angle / h.noise_size;
			const float n = lerp(rmsdf_value_noise(q + 97.0 * rmsdf_hash(float2(k, 1.7))),
				rmsdf_value_noise(q + 97.0 * rmsdf_hash(float2(k + 1.0, 1.7))), smoothstep(0.0, 1.0, time - k));
			grain *= lerp(1.0, 0.4 + 1.2 * n, saturate(h.noise * depth));
		}
		// pulse-width dimming shows as flicker at the lowest steps only
		if (h.flicker > 0.0)
			grain *= 1.0 - h.flicker * saturate(1.0 - illumination.w / 0.35) * step(0.55, frac(timers.x * 9.7));
		light *= level * grain;
	}
	return light;
}

#endif
