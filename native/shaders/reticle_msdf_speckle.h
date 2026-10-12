// Laser speckle: correlated complex amplitudes, squared to give bright grains and dark gaps.
#ifndef RETICLE_MSDF_SPECKLE_H
#define RETICLE_MSDF_SPECKLE_H

float2 rmsdf_speckle_wave(float2 cell)
{
	const float power = -log(max(rmsdf_hash(cell), 1e-4));
	const float phase = 6.2831853 * rmsdf_hash(cell + float2(43.7, 19.3));
	float s, c;
	sincos(phase, s, c);
	return sqrt(power) * float2(c, s);
}

float rmsdf_speckle_field(float2 p)
{
	const float2 cell = floor(p);
	const float2 f = p - cell;
	const float2 u = f * f * (3.0 - 2.0 * f);
	const float2 wave = lerp(lerp(rmsdf_speckle_wave(cell), rmsdf_speckle_wave(cell + float2(1, 0)), u.x),
		lerp(rmsdf_speckle_wave(cell + float2(0, 1)), rmsdf_speckle_wave(cell + float2(1, 1)), u.x), u.y);
	// Interpolation reduces the amplitude variance; compensate to keep the mean light at one.
	const float2 variance = u * u + (1.0 - u) * (1.0 - u);
	return dot(wave, wave) / (variance.x * variance.y);
}

float rmsdf_speckle(float2 p, float2 dx, float2 dy)
{
	// Average the pixel footprint before emitting light; unresolved grains lose contrast rather than crawl.
	const float2 a = 0.25 * (dx + dy), b = 0.25 * (dx - dy);
	const float power = 0.25 * (rmsdf_speckle_field(p + a) + rmsdf_speckle_field(p - a)
		+ rmsdf_speckle_field(p + b) + rmsdf_speckle_field(p - b));
	const float footprint = max(length(dx), length(dy));
	return lerp(1.0, power, 1.0 - smoothstep(1.0, 2.5, footprint));
}

#endif
