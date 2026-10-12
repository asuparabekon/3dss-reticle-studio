// Self-describing MSDF reticle atlas (formats 3 and 4, format 2 still reads), shared evaluation for the stock 3DSS lens, PiP and Reference.
// The atlas, its header and the per-mip descriptor are produced by tools/reticle_msdf in the GAMMA patch repo.
//
// A tile stores a multi-channel signed distance in RGB (regenerated per mip, one tile-normalised range) and the
// exact area coverage of every texel in A. Magnified, the median distance gives a crisp edge. Minified, a few
// taps on the coverage mips integrate the pixel footprint, so a sub-pixel line fades with its true darkness.
// Format 4 is BC5: a tile holds one true signed distance in R and the coverage in G, or (a multi-channel tile) R and
// G of the multi-channel distance with B and the coverage in the square to its right. Its header texels are 4x4
// blocks that carry their four bytes exactly in the endpoints: R0, G0 on the block's top row, R1, G1 below.
//
// Evaluation only; every path composes the result its own way.
#ifndef RETICLE_MSDF_H
#define RETICLE_MSDF_H

#define RMSDF_ROLE_INK 1
#define RMSDF_ROLE_ILLUM 2
#define RMSDF_ROLE_PAINT 3
#define RMSDF_FLAG_ILLUMINATES 1
#define RMSDF_FLAG_PAINT 2
#define RMSDF_FLAG_HOLO 4
#define RMSDF_FLAG_SFP 8
#define RMSDF_FLAG_OWN_COLOURS 16
#define RMSDF_TILE_PAINT_GLOWS 1
#define RMSDF_VERSION 3
#define RMSDF_VERSION_BC5 4
#define RMSDF_TILE_MSDF 2
#define RMSDF_DEFAULT_LUMINANCE 8.0
#define RMSDF_MAX_TILES 16
#define RMSDF_GUTTER 0.0625
#define RMSDF_UNITS_PER_RADIAN_MOA 3437.74677
#define RMSDF_UNITS_PER_RADIAN_MRAD 1000.0

#ifndef RETICLE_MSDF_PARAMS_DECLARED
#define RETICLE_MSDF_PARAMS_DECLARED
// engine settings: x holographic level, y brightness, z holographic speckle depth, w line weight (0 realistic .. 1 bold)
uniform float4 reticle_msdf_params;
// x 1 = the engine takes the reticle glow into its own target, y fail-closed debug tint,
// z 1 = distance field rendering (0 = r__reticle_msdf 0: coverage only, legacy colouring), w 1 = params are set
uniform float4 reticle_msdf_control;
// x 1 = the reticle glow targets are bound at slots 2 and 3 for this reflex draw, -1 = a reflex draw the engine
// repeats later: no MSDF light now; y 1 = the lenses' copies of the glow targets hold the frame's reflex glow
uniform float4 reticle_msdf_reflex_glow;
#endif

struct ReticleAtlas
{
	bool recognised; // the header magic is present: never treat the texture as a legacy bitmap
	bool valid;      // every header, descriptor and tile check passed
	bool packed;     // format 4 (BC5): header texels are blocks, tiles hold R, G
	float2 dims;     // resident mip 0 size in texels; tile sizes are fractions of the width
	uint orig_mip;
	uint tile_count;
	uint flags;
	uint unit;
	uint desc_width;
	uint state_count;
	uint material_count;
	uint colour_mode; // 0 authored, 1 3DSS; meaningful for formats 5/6
	uint mark_count; // Mark Switch marks the atlas draws, at least 1
	uint desc_base;  // descriptor byte of the first tile record (format 2: 8, format 3: 40)
	float calibrated_magnification;
	float legacy_span;   // units across one UV of the bitmap the atlas replaces (0 = true angular mapping)
	float luminance;     // lit core at the top brightness step, scene luminance
	float glow_strength; // the glow's energy relative to the core
	uint step_count;     // authored brightness steps (0 = the Mark Switch colour's magnitude)
	uint2 steps;         // brightness * 255 of each Mark Switch mark from the first, four bytes per component
};

struct ReticleTile
{
	uint role;
	uint material;
	uint flags;
	float3 colour;
	uint level_max;
	float2 uv0;
	float uv_size;
	float2 centre;
	float half_extent;
	float region_half;
	float range;
	float stroke_texels; // thinnest stroke at mip 0, 0 = unknown
	uint mark;           // Mark Switch mark 1..15 the tile belongs to, 0 = every mark
	bool multi;          // a multi-channel distance (format 4: B and coverage in the square to the right)
};

struct ReticleMarks
{
	float ink;         // etched coverage, opaque black (includes unlit illuminated elements)
	float3 illum_rgb; // coverage-weighted authored light colour (formats 5/6)
	float illum;       // coverage of the illuminated elements
	float paint;       // painted coverage
	float3 paint_rgb;  // painted albedo, coverage weighted
	float paint_glow;  // painted coverage that lights up with the illumination
};

// header texel (x, y): a texel, or in a format 4 atlas the endpoints of block (x, y)
uint4 rmsdf_texel(Texture2D t, bool packed, uint x, uint y)
{
	if (!packed)
		return uint4(round(t.Load(int3(x, y, 0)) * 255.0));
	return uint4(round(float4(t.Load(int3(4 * x, 4 * y, 0)).rg, t.Load(int3(4 * x, 4 * y + 1, 0)).rg) * 255.0));
}

uint rmsdf_byte(Texture2D t, ReticleAtlas a, uint i)
{
	const uint k = i >> 2;
	const uint4 v = rmsdf_texel(t, a.packed, k % a.desc_width, 1 + k / a.desc_width);
	const uint c = i & 3;
	return c == 0 ? v.x : (c == 1 ? v.y : (c == 2 ? v.z : v.w));
}

uint rmsdf_u16(Texture2D t, ReticleAtlas a, uint i)
{
	return rmsdf_byte(t, a, i) | (rmsdf_byte(t, a, i + 1) << 8);
}

float rmsdf_s16(Texture2D t, ReticleAtlas a, uint i)
{
	const uint v = rmsdf_u16(t, a, i);
	return v >= 32768 ? float(v) - 65536.0 : float(v);
}

#include "reticle_msdf_states.h"

ReticleTile reticle_tile(Texture2D t, ReticleAtlas a, uint index)
{
	const uint b = a.desc_base + 24 * index;
	ReticleTile r;
	r.role = rmsdf_byte(t, a, b);
	r.material = a.desc_base >= 1144 ? rmsdf_byte(t, a, 168 + index) : 0;
	r.flags = rmsdf_byte(t, a, b + 1);
	r.colour = float3(rmsdf_byte(t, a, b + 2), rmsdf_byte(t, a, b + 3), rmsdf_byte(t, a, b + 4)) / 255.0;
	r.level_max = rmsdf_byte(t, a, b + 5);
	r.uv0 = float2(rmsdf_u16(t, a, b + 6), rmsdf_u16(t, a, b + 8)) / 65536.0;
	r.uv_size = rmsdf_u16(t, a, b + 10) / 65536.0;
	r.centre = float2(rmsdf_s16(t, a, b + 12), rmsdf_s16(t, a, b + 14)) / 64.0;
	r.half_extent = rmsdf_u16(t, a, b + 16) / 64.0;
	r.region_half = rmsdf_u16(t, a, b + 18) / 64.0;
	r.range = rmsdf_u16(t, a, b + 20) / 65536.0;
	r.stroke_texels = rmsdf_u16(t, a, b + 22) / 256.0;
	r.mark = r.flags >> 4;
	r.multi = !a.packed || (r.flags & RMSDF_TILE_MSDF) != 0;
	return r;
}

ReticleAtlas reticle_atlas_open(Texture2D t)
{
	ReticleAtlas a = (ReticleAtlas)0;
	uint w, h, levels;
	t.GetDimensions(0, w, h, levels);
	if (w < 8 || h < 8)
		return a;
	const uint4 magic0 = uint4(0x52, 0x4D, 0x53, 0x44), magic1 = uint4(0x46, 0x41, 0x54, 0x4C);
	a.packed = any(rmsdf_texel(t, false, 0, 0) != magic0);
	if (a.packed && (w < 32 || h < 8 || any(rmsdf_texel(t, true, 0, 0) != magic0)))
		return a;
	if (any(rmsdf_texel(t, a.packed, 1, 0) != magic1))
		return a;
	a.recognised = true;

	const uint4 t0 = magic0;
	const uint4 t1 = magic1;
	const uint4 t2 = rmsdf_texel(t, a.packed, 2, 0);
	const uint4 t3 = rmsdf_texel(t, a.packed, 3, 0);
	const uint4 t4 = rmsdf_texel(t, a.packed, 4, 0);
	const uint4 t5 = rmsdf_texel(t, a.packed, 5, 0);
	const uint4 t6 = rmsdf_texel(t, a.packed, 6, 0);
	const uint4 t7 = rmsdf_texel(t, a.packed, 7, 0);
	const uint4 s = t0 + t1 + t2 + t3 + t4 + t5 + t6;
	const uint header_sum = s.x + s.y + s.z + s.w;
	a.orig_mip = t2.y;
	a.tile_count = t3.z;
	a.desc_width = t4.x;
	a.unit = t4.z;
	a.flags = t4.w;
	a.dims = float2(w, h);
	a.desc_base = t2.x >= 7 ? 1144 : t2.x >= 5 ? 168 : (t2.x >= 3 ? 40 : 8);
	const uint descriptor_texels = t4.y + (t2.x >= 7 ? (t5.w << 8) : 0);
	bool ok = (a.packed ? (t2.x == RMSDF_VERSION_BC5 || t2.x == 6 || t2.x == 8) : (t2.x == RMSDF_VERSION || t2.x == 2 || t2.x == 5 || t2.x == 7))
		&& (1u << t2.z) == w && (1u << t2.w) == h
		&& all(t6 == uint4(0xA5, 0x5A, 0xC3, 0x3C))
		&& t7.x == (header_sum & 255) && t7.y == ((header_sum >> 8) & 255)
		&& t7.z == 255 - t2.y && t7.w == 0x96
		&& a.tile_count >= 1 && a.tile_count <= RMSDF_MAX_TILES
		&& descriptor_texels == a.desc_base / 4 + 6 * a.tile_count && a.desc_width > 0
		&& t3.y >= 1 && t2.y + t3.y <= t3.x && levels >= t3.y
		&& (a.unit == 1 || a.unit == 2);
	if (!ok)
		return a;

	uint desc_sum = 0;
	[loop] for (uint k = 0; k < descriptor_texels; ++k)
	{
		const uint4 v = rmsdf_texel(t, a.packed, k % a.desc_width, 1 + k / a.desc_width);
		desc_sum += v.x + v.y + v.z + v.w;
	}
	a.mark_count = rmsdf_byte(t, a, 7);
	ok = t5.x == (desc_sum & 255) && t5.y == ((desc_sum >> 8) & 255) && rmsdf_byte(t, a, 6) == a.tile_count
		&& a.mark_count >= 1 && a.mark_count <= 15;
	a.material_count = a.desc_base >= 1144 ? rmsdf_byte(t, a, 42) : 0;
	ok = ok && (a.desc_base < 1144 || (a.material_count >= 1 && a.material_count <= 16));
	const float2 cell = float(1u << t5.z) / a.dims;
	const float aspect = a.dims.x / a.dims.y;
	[loop] for (uint i = 0; i < a.tile_count && ok; ++i)
	{
		const ReticleTile r = reticle_tile(t, a, i);
		ok = r.role >= RMSDF_ROLE_INK && r.role <= RMSDF_ROLE_PAINT
			&& r.uv_size > 0.0 && r.uv0.x + r.uv_size * (a.packed && r.multi ? 2.0 : 1.0) <= 1.0
			&& r.uv0.y + r.uv_size * aspect <= 1.0
			&& !(r.uv0.x < cell.x && r.uv0.y < cell.y)
			&& r.half_extent > 0.0 && r.region_half > 0.0 && r.range > 0.0
			&& r.level_max >= a.orig_mip && r.level_max - a.orig_mip < levels && r.level_max < t3.x
			&& r.mark <= a.mark_count
			&& (a.material_count == 0 || r.material < a.material_count);
	}
	if (a.desc_base >= 168)
	{
		a.state_count = rmsdf_byte(t, a, 40);
		a.colour_mode = rmsdf_byte(t, a, 41);
		ok = ok && a.state_count >= 1 && a.state_count <= 15 && a.colour_mode <= 1;
		[loop] for (uint state = 0; state < a.state_count && ok; ++state)
		{
			const ReticleState entry = reticle_state(t, a, float(state));
			ok = entry.pattern >= 1 && entry.pattern <= a.mark_count && entry.flags <= 1;
		}
	}
	a.mark_count = max(a.mark_count, 1u);
	a.calibrated_magnification = max(rmsdf_u16(t, a, 0) / 100.0, 0.01);
	a.luminance = RMSDF_DEFAULT_LUMINANCE;
	a.glow_strength = 1.0;
	if (a.desc_base >= 40)
	{
		a.legacy_span = rmsdf_u16(t, a, 8) / 8.0;
		const uint luminance = rmsdf_u16(t, a, 10);
		a.luminance = luminance > 0 ? luminance / 16.0 : RMSDF_DEFAULT_LUMINANCE;
		a.glow_strength = rmsdf_byte(t, a, 12) / 32.0;
		a.step_count = min(rmsdf_byte(t, a, 14), 8u);
		a.steps = uint2(rmsdf_byte(t, a, 16) | rmsdf_byte(t, a, 17) << 8 | rmsdf_byte(t, a, 18) << 16 | rmsdf_byte(t, a, 19) << 24,
			rmsdf_byte(t, a, 20) | rmsdf_byte(t, a, 21) << 8 | rmsdf_byte(t, a, 22) << 16 | rmsdf_byte(t, a, 23) << 24);
	}
	a.valid = ok;
	return a;
}

float rmsdf_units_per_radian(ReticleAtlas a)
{
	return a.unit == 2 ? RMSDF_UNITS_PER_RADIAN_MRAD : RMSDF_UNITS_PER_RADIAN_MOA;
}

// Reticle angle (units, x right, y down) of a world direction given in tangent coordinates (x right, y up)
// relative to the bore direction, measured on the bore's tangent plane.
float2 reticle_msdf_angle(ReticleAtlas a, float2 view_tangent, float2 bore_tangent)
{
	const float3 f = normalize(float3(bore_tangent, 1.0));
	const float3 r = normalize(cross(float3(0.0, 1.0, 0.0), f));
	const float3 u = cross(f, r);
	const float3 d = float3(view_tangent, 1.0);
	const float3 p = d / dot(d, f);
	return float2(dot(p, r), -dot(p, u)) * rmsdf_units_per_radian(a);
}

// View tangent (x right, y up) of a frame UV, tan_half = tangent of the half FOV along x and y.
float2 reticle_msdf_frame_tangent(float2 frame_uv, float2 tan_half)
{
	return float2(2.0 * frame_uv.x - 1.0, 1.0 - 2.0 * frame_uv.y) * tan_half;
}

// View tangent of an image UV rendered with projection (1/_11, 1/_22, _31, _32): tangents per NDC unit and the
// principal point offset in NDC.
float2 reticle_msdf_projected_tangent(float2 uv, float4 projection)
{
	return (float2(2.0 * uv.x - 1.0, 1.0 - 2.0 * uv.y) - projection.zw) * projection.xy;
}

// Scale mode 3dss: the reticle follows the bitmap it replaces. legacy_uv is the coordinate the 3DSS shaders would
// sample that bitmap at (the Mark Switch cell's UV for grid textures), centred on the reticle.
bool reticle_msdf_legacy_scale(ReticleAtlas a)
{
	return a.legacy_span > 0.0;
}

float2 reticle_msdf_legacy_angle(ReticleAtlas a, float2 legacy_uv)
{
	return (legacy_uv - 0.5) * a.legacy_span;
}

float rmsdf_median(float3 v)
{
	return max(min(v.r, v.g), min(max(v.r, v.g), v.b));
}

// r__reticle_msdf 0: the atlas draws from its coverage alone with the legacy reticle colouring
bool reticle_msdf_legacy()
{
	return reticle_msdf_control.w > 0.5 && reticle_msdf_control.z < 0.5;
}

// Holographic sights widen the filter footprint by this much (reticle_msdf_reflex.h sets it)
static float rmsdf_soften = 1.0;

// The tile's signed distance at uv (0.5 = edge): the multi-channel median, or a format 4 tile's one distance
float rmsdf_distance(Texture2D t, SamplerState smp, ReticleAtlas a, ReticleTile r, float2 uv)
{
	const float4 v = t.SampleLevel(smp, uv, 0);
	if (!a.packed)
		return rmsdf_median(v.rgb);
	if (!r.multi)
		return v.r;
	return rmsdf_median(float3(v.rg, t.SampleLevel(smp, uv + float2(r.uv_size, 0.0), 0).r));
}

// Where the tile's coverage is read: the offset from its distance texels and the channel
void rmsdf_coverage_at(ReticleAtlas a, ReticleTile r, out float2 offset, out float4 channel)
{
	offset = float2(a.packed && r.multi ? r.uv_size : 0.0, 0.0);
	channel = a.packed ? float4(0, 1, 0, 0) : float4(0, 0, 0, 1);
}

// Bilinear coverage with a zero border around one material tile. Wide minification
// taps must never read adjacent materials, distance channels or descriptor bytes.
float rmsdf_material_area(Texture2D t, SamplerState smp, ReticleAtlas a, ReticleTile r,
	float2 uv, float lod, float2 offset, float4 channel)
{
	const float2 lo = r.uv0 + offset;
	const float2 hi = lo + r.uv_size * float2(1.0, a.dims.x / a.dims.y);
	const float2 dims = a.dims / exp2(lod);
	const float2 half_pixel = 0.5 / dims;
	const float2 weight = saturate((uv - lo) * dims + 0.5) * saturate((hi - uv) * dims + 0.5);
	return dot(t.SampleLevel(smp, clamp(uv, lo + half_pixel, hi - half_pixel), lod), channel) * weight.x * weight.y;
}

float rmsdf_area(Texture2D t, SamplerState smp, ReticleAtlas a, ReticleTile r,
	float2 uv, float lod, float2 offset, float4 channel)
{
	uv += offset;
	if (a.material_count == 0) return dot(t.SampleLevel(smp, uv, lod), channel);
	const float base = floor(lod);
	return lerp(rmsdf_material_area(t, smp, a, r, uv, base, offset, channel),
		rmsdf_material_area(t, smp, a, r, uv, base + 1.0, offset, channel), frac(lod));
}

// Coverage of one tile at reticle angle p; jx/jy = reticle angle per screen pixel along x/y.
float rmsdf_tile_coverage(Texture2D t, SamplerState smp, ReticleAtlas a, ReticleTile r, float2 p, float2 jx, float2 jy)
{
	const float scale = 0.5 / r.half_extent;
	const float2 uv = r.uv0 + ((p - r.centre) * scale + 0.5) * r.uv_size * float2(1.0, a.dims.x / a.dims.y);
	const float texels = r.uv_size * a.dims.x;
	const float2 tx = jx * scale * texels;
	const float2 ty = jy * scale * texels;
	const float xx = dot(tx, tx), yy = dot(ty, ty), xy = dot(tx, ty);
	const float mean = 0.5 * (xx + yy);
	const float spread = sqrt(max(0.25 * (xx - yy) * (xx - yy) + xy * xy, 0.0));
	const float footprint = sqrt(mean + spread); // texels per pixel along the long axis
	const float lod_max = max(float(r.level_max) - float(a.orig_mip), 0.0);
	// the distance field draws edges only where it resolves the thinnest stroke (not on a stripped top mip
	// of a fine tile); elsewhere the coverage mips take over
	const float stroke = r.stroke_texels > 0.0 ? r.stroke_texels / float(1u << a.orig_mip) : 1e9;
	const float soften = max(rmsdf_soften, 1.0);
	const float minified = reticle_msdf_legacy() ? 1.0
		: max(saturate(footprint * soften - 1.0), 1.0 - saturate((stroke - 0.75) / 0.5));
	float2 coverage_offset;
	float4 coverage_channel;
	rmsdf_coverage_at(a, r, coverage_offset, coverage_channel);
	float edge = 0.0;
	if (minified < 1.0)
	{
		const float sd = rmsdf_distance(t, smp, a, r, uv) - 0.5;
		edge = saturate(0.5 + sd * r.range * texels / max(sqrt(mean) * soften, 1e-4));
		// far from every shape the channels can still meet near 0.5; a coarse coverage mip says whether a shape
		// is within a few texels at all
		if (rmsdf_area(t, smp, a, r, uv, min(3.0, lod_max), coverage_offset, coverage_channel) <= 0.0)
			edge = 0.0;
	}
	float area = 0.0;
	if (minified > 0.0)
	{
		// 2x2 taps over the pixel footprint, 4 along an axis that is twice as long as the other; a softened
		// footprint spreads 4x4 taps over the wider area and keeps the mip of the true one, so it stays round
		// past the tile's last usable mip more taps keep covering the footprint
		const float bx = xx > 4.0 * yy ? 4.0 : 2.0;
		const float by = yy > 4.0 * xx ? 4.0 : 2.0;
		const float lod_raw = log2(max(max(sqrt(xx) / bx, sqrt(yy) / by), 1e-4)) + 1.4;
		const float lod = clamp(lod_raw, 0.0, lod_max);
		const float extra = exp2(clamp(lod_raw - lod_max, 0.0, 2.0));
		const uint nx = soften > 1.0 ? max(4u, uint(min(ceil(bx * extra), 8.0))) : uint(min(ceil(bx * extra), 8.0));
		const uint ny = soften > 1.0 ? max(4u, uint(min(ceil(by * extra), 8.0))) : uint(min(ceil(by * extra), 8.0));
		[loop] for (uint i = 0; i < nx; ++i)
		{
			[loop] for (uint j = 0; j < ny; ++j)
			{
				const float2 o = (((i + 0.5) / nx - 0.5) * tx + ((j + 0.5) / ny - 0.5) * ty) * soften;
				area += rmsdf_area(t, smp, a, r, uv + o / a.dims, lod, coverage_offset, coverage_channel);
			}
		}
		area /= float(nx * ny);
	}
	return lerp(edge, area, minified);
}

float reticle_msdf_line_weight()
{
	return reticle_msdf_control.w > 0.5 ? saturate(reticle_msdf_params.w) : 0.0;
}

// All marks at reticle angle p for Mark Switch mark `current` (markswitch_current, 0 based). Tiles of one role
// and mark are ordered fine to coarse; a finer tile owns its region and hands over to the next one across a 5%
// band, the last tile of a group owns everything up to its gutter. Tiles of mark 0 belong to every mark.
ReticleMarks reticle_msdf_marks(Texture2D t, SamplerState smp, ReticleAtlas a, float2 p, float2 jx, float2 jy,
	float current)
{
	ReticleMarks m = (ReticleMarks)0;
	const float gamma = 1.0 - 0.75 * reticle_msdf_line_weight();
	const ReticleState state = reticle_state(t, a, current);
	const uint selected = state.pattern;
	float remaining = 1.0;
	float group_cov = 0.0;
	[loop] for (uint i = 0; i < a.tile_count; ++i)
	{
		const ReticleTile r = reticle_tile(t, a, i);
		bool last = i + 1 == a.tile_count;
		if (!last)
		{
			const ReticleTile n = reticle_tile(t, a, i + 1);
			last = n.role != r.role || any(n.colour != r.colour) || n.mark != r.mark || n.material != r.material;
		}
		const bool shown = (r.mark == 0 || r.mark == selected)
			&& (state.hidden_materials & (1u << r.material)) == 0;
		const float2 d = abs(p - r.centre);
		const float reach = max(d.x, d.y);
		// the last tile reaches into its gutter so a stroke on the region edge keeps its filter footprint
		const float limit = last ? r.half_extent * (1.0 - RMSDF_GUTTER) : r.region_half;
		if (shown && remaining > 0.0 && reach <= limit)
		{
			const float w = last ? 1.0 : saturate((r.region_half - reach) / (0.05 * r.region_half));
			const float cov = pow(saturate(rmsdf_tile_coverage(t, smp, a, r, p, jx, jy)), gamma);
			group_cov += remaining * w * cov;
			remaining *= 1.0 - w;
		}
		if (last)
		{
			const float4 material = reticle_material(t, a, current, r.material, r.colour);
			if (r.role == RMSDF_ROLE_INK)
				m.ink = 1.0 - (1.0 - m.ink) * (1.0 - group_cov);
			else if (r.role == RMSDF_ROLE_ILLUM)
			{
				m.illum = 1.0 - (1.0 - m.illum) * (1.0 - group_cov);
				if (a.state_count > 0)
				{
					const float3 hue = a.material_count > 0
						? (a.colour_mode == 0 ? material.rgb : float3(1, 1, 1)) * material.a
						: (a.colour_mode == 0 && state.flags == 0 ? r.colour : float3(1, 1, 1));
					m.illum_rgb += hue * group_cov;
				}
			}
			else
			{
				m.paint = 1.0 - (1.0 - m.paint) * (1.0 - group_cov);
				m.paint_rgb += material.rgb * group_cov;
				if (r.flags & RMSDF_TILE_PAINT_GLOWS)
					m.paint_glow = 1.0 - (1.0 - m.paint_glow) * (1.0 - group_cov);
			}
			remaining = 1.0;
			group_cov = 0.0;
		}
	}
	if (a.state_count == 0)
		m.ink = 1.0 - (1.0 - m.ink) * (1.0 - m.illum);
	return m;
}

// The 3DSS Mark Switch colours are made for blending a bitmap; emitted as light they read pale, so the lit hue
// is saturated by raising each channel, relative to the brightest, to this power
#define RMSDF_HUE_SATURATION 3.0

// Illumination from the Mark Switch colour of the current mark (markswitch_color.rgb, black = off; its alpha
// carries the dot offset). x,y,z = hue at full value, w = brightness 0..1 (0 = off): the atlas's authored step
// for this mark, or else the colour's magnitude
float4 reticle_msdf_illumination(ReticleAtlas a, float4 colour, float current)
{
	const float peak = max(colour.r, max(colour.g, colour.b));
	if (peak <= 0.0)
		return 0.0;
	float level = saturate(peak);
	if (a.step_count > 0)
	{
		const uint i = min(uint(max(current, 0.0)), a.step_count - 1);
		level = ((i < 4 ? a.steps.x : a.steps.y) >> (8 * (i & 3)) & 255) / 255.0;
	}
	return float4(pow(colour.rgb / peak, RMSDF_HUE_SATURATION), level);
}

// The illumination of Mark Switch mark `current`: the sight's colour, or, while that is black on an atlas whose
// marks carry their own colour (a sight with no ms_colors, like the 3DSS EOTech), the colour of the mark.
float4 reticle_msdf_lighting(Texture2D t, ReticleAtlas a, float4 colour, float current)
{
	if (a.state_count > 0)
	{
		const ReticleState state = reticle_state(t, a, current);
		if (a.material_count > 0)
		{
			if (a.colour_mode == 0) return float4(1, 1, 1, 1);
			const float peak = max(colour.r, max(colour.g, colour.b));
			return peak > 0.0 ? float4(pow(colour.rgb / peak, RMSDF_HUE_SATURATION), saturate(peak)) : 0.0;
		}
		if (a.colour_mode == 0)
			return float4(state.flags != 0 ? state.colour : float3(1, 1, 1), state.brightness);
		const float peak = max(colour.r, max(colour.g, colour.b));
		return peak > 0.0 ? float4(pow(colour.rgb / peak, RMSDF_HUE_SATURATION), saturate(peak) * state.brightness) : 0.0;
	}
	const float4 lit = reticle_msdf_illumination(a, colour, current);
	if (lit.w > 0.0 || !(a.flags & RMSDF_FLAG_OWN_COLOURS) || !a.valid)
		return lit;
	const uint selected = uint(max(current, 0.0)) % a.mark_count + 1;
	[loop] for (uint i = 0; i < a.tile_count; ++i)
	{
		const ReticleTile r = reticle_tile(t, a, i);
		if (r.role == RMSDF_ROLE_ILLUM && (r.mark == selected || r.mark == 0))
			return reticle_msdf_illumination(a, float4(r.colour, 1.0), current);
	}
	return lit;
}

// The lit core in scene luminance, unbounded and premultiplied by coverage. The engine scales it by the eye's
// exposure, spreads its glow and shoulders it toward white where it exceeds the display (the engine's 3DSS reticle glow).
// r__reticle_msdf 0 gives the plain legacy colouring in display units instead.
float3 reticle_msdf_emission(ReticleAtlas a, ReticleMarks m, float4 illumination)
{
	if (illumination.w <= 0.0)
		return 0.0;
	const float3 core = a.state_count > 0 ? m.illum_rgb : float3(max(m.illum, m.paint_glow), max(m.illum, m.paint_glow), max(m.illum, m.paint_glow));
	if (reticle_msdf_legacy())
		return illumination.rgb * (illumination.w * core);
	const float brightness = reticle_msdf_control.w > 0.5 ? max(reticle_msdf_params.y, 0.0) : 1.0;
	return illumination.rgb * (core * illumination.w * a.luminance * brightness);
}

// Light added straight into the scene when the engine has no glow target: no glow, the hue kept by a soft clip
float3 reticle_msdf_inline(float3 emission)
{
	if (reticle_msdf_legacy())
		return emission;
	const float peak = max(emission.r, max(emission.g, emission.b));
	return peak > 0.0 ? emission * ((1.0 - exp(-peak)) / peak) : 0.0;
}

// Outputs for the engine's glow targets: the core, and the same light weighted by the atlas's glow strength,
// which the engine spreads into the glow. Alpha is 1 where there is light and 0 elsewhere, so a source-alpha
// blend (the stock lens, reflex sights) writes the light and leaves other pixels as they were.
float4 reticle_msdf_core_out(float3 emission)
{
	return float4(emission, any(emission > 0.0) ? 1.0 : 0.0);
}

float4 reticle_msdf_glow_out(ReticleAtlas a, float3 emission)
{
	return float4(emission * a.glow_strength, any(emission > 0.0) ? 1.0 : 0.0);
}

// The glow source of an emission pass whose shader returns the core (scope_custom_reticle.h); the pass's
// wrapper writes it as its second output
static float4 rmsdf_glow_source = 0;

// Reflective paint: albedo times the luminance of the light behind the reticle, never emissive.
float3 reticle_msdf_paint(ReticleMarks m, float background_luminance)
{
	return m.paint > 0.0 ? m.paint_rgb / m.paint * background_luminance : 0.0;
}

bool reticle_msdf_emission_external()
{
	return reticle_msdf_control.x > 0.5;
}

#endif
