# Stereo track: DSP library

Three numpy-only modules with no app surface yet: `stereo_cues.py` (measure where a voice sits),
`stereo_render.py` (place a mono clip there) and `loudness.py` (match its level). They are written from
the published methods: GCC-PHAT (Knapp and Carter, 1976), the covariance-based relative transfer function
(Gannot et al., 2001) and ITU-R BS.1770-4 loudness. The goal is a stereo track that follows the original
voice's left/right position and is loudness-matched, usable with or without dubbing.

## Conventions
- Positive ILD means the left channel is louder; positive ITD means the right channel arrives later
  (tR - tL). Both mean "source toward the left". GCC-PHAT on `L * conj(R)` peaks at tL - tR, so the code negates it.
- Renderers return `(samples, 2)` float64 of the mono clip's length, power preserving
  (`g_L^2 + g_R^2 = 2`), unclipped.

## Cues (`stereo_cues.py`)
- STFT: 1024-sample frames, 75% overlap (hop 256), sqrt-Hann analysis and synthesis (their product is a Hann
  that sums to 2), zero-padded to 2048 so delays of either sign never wrap. `stft`/`istft` round-trip exactly.
- ILD per frame over 28 log-spaced bands from 100 Hz to min(18 kHz, 0.45 sr):
  `10 log10((sum|L|^2 + eps) / (sum|R|^2 + eps))`.
- ITD per frame: GCC-PHAT over 180-6500 Hz bins (`L conj(R) / |L conj(R)|`), `irfft` with 4x zero padding
  (a lag grid of 0.25 sample), peak searched within +-0.9 ms and refined by a parabola. The peak height, normalised to 1 for
  a perfectly coherent delay, is the confidence.
- A frame is reliable when its stereo energy is more than 10 dB above the 20th-percentile frame energy of the
  span **and** its GCC peak exceeds 0.25. The span is whatever is passed in, so pass one line at a time. A
  steady span with no quiet part has no reliable frames by design.
- `stabilize`: unreliable frames hold the previous value and decay toward the line median (0.3 s time
  constant); before the first reliable frame they sit at the median; a line with no reliable frames is centred
  (0 dB, 0 ms). `smooth` is a causal one-pole (150 ms), so tracks lag the voice by about that long.
  `track_cues` = estimate, stabilize, smooth.
- Band gains are interpolated to FFT bins on a log-frequency axis, ends held.
- Cue tracks are stretched over normalised time to the clip's length, so a dub of another duration follows
  the same left-to-right path.

## Rendering (`stereo_render.py`)
- `static`: one broadband ILD and ITD per line (medians), applied as a gain and a linear-phase fractional delay (+-tau/2 per channel).
- `follow`: per frame, `r = 10^(ILD/10)`, `g_L = sqrt(2r/(1+r))`, `g_R = sqrt(2/(1+r))` per band (interpolated to
  bins) and a +-tau/2 linear-phase delay, in the STFT domain.
- `rtf` (experimental): per 10 s block (50% overlapping Hann crossfade of the filters),
  `H_ch = sum_t w_t X_ch conj(X_ref) / (sum_t w_t |X_ref|^2 + eps)` with the mid `(L+R)/2` as reference and the
  frame reliability as `w_t`. The estimate is shrunk toward the parametric ILD/ITD model by the per-bin
  coherence, magnitudes are smoothed over 1/3 octave, the L/R ratio is capped at +-20 dB and the pair is
  normalised to `|H_L|^2 + |H_R|^2 = 2`. Needs the stereo source as well as its cues. Optional level follow: the
  reference level per frame relative to the line median, smoothed (300 ms), clamped to +-3 dB.

## Loudness (`loudness.py`)
- K-weighting is the two BS.1770-4 biquads (high shelf, high-pass). Their analytic response `|H_K(f)|^2` is
  applied to each block's power spectrum, which avoids scipy. Coefficients reproduce the standard's 48 kHz table
  exactly and use the same design equations at other rates.
- 400 ms blocks every 100 ms, absolute gate -70 LUFS, relative gate -10 LU. A line shorter than one block has
  nothing to gate: it reads as the ungated K-weighted mean square. Silence (and empty or non-finite input) returns
  `LUFS_FLOOR` (-70), never NaN.
- A 997 Hz mono sine at 0 dBFS reads -3.01 LUFS: `-0.691 + 10 log10(0.5) + 10 log10(|H_K(997)|^2)` with
  `|H_K(997)|^2 = +0.691 dB` by design of the offset.
- Gating keeps blocks that straddle a loud/quiet join, as the standard does, so a gated value sits slightly below the loud part alone.
- `match_gain(source, clip, boost_cap_db=6)`: boost capped (the cap is clamped to 0-12 dB), attenuation unlimited,
  an optional peak guard (`clip_peak`, ceiling -1 dBFS) that limits boosts only, and 1.0 when either loudness is silent.
- Measure the source line on separated vocals when they exist. Measured on the full mix the background inflates
  the source loudness, and the matched clip ends up too loud.

## Measured accuracy (synthetic, 16 kHz, `tests/test_stereo_*.py`)
- Full-band speech-like noise with known ITD -0.6/0/+0.4 ms and ILD -8/0/+6 dB: ITD error under 0.01 ms on every
  reliable frame; per-frame ILD (median over 300-5000 Hz bands) within 0.9 dB, single narrow bands within ~2.3 dB.
- A source limited to 200-3500 Hz: worst-frame ITD error about 0.09 ms and GCC peak about half, because PHAT
  weights empty bins equally and leakage carries phase error.
- Uncorrelated noise at 10 dB SNR: ITD within 0.01 ms (full band).
- Moving source (ITD ramp 1 ms over 3 s): raw tracks within 0.01 ms; smoothed tracks follow the truth delayed by the
  smoothing lag within 0.07 ms on measured frames.
- A decoy as loud as the voice at another ITD takes over the estimate completely, and the GCC peak stays near 1,
  so the reliability mask cannot detect it.
- Render, then re-estimate on the output: ITD and ILD return within 0.05 ms and 1 dB for all three tiers.
- `rtf` with 10 s vs 3 s blocks: identical (< 1e-4) for a gain-only placement; with a delay about 1.4e-3 of a
  1.1 peak, from frame-window scatter.

## Limits
- One dominant source per line is assumed. Ear cleaning, rain and mouth sounds louder than the voice bias
  the cues (the decoy case above), and nothing flags it.
- Fast movement is smeared by the 150 ms smoothing, and unreliable gaps decay toward the line median.
- The 0.25 peak threshold and the 10 dB energy margin are untuned on real speech; narrow-band material lowers peak
  heights. The cap on the ITD search (+-0.9 ms) excludes wider spacings.
- `rtf` assumes the room response fits inside one 1024-sample frame, uses line-level parametric fallback and is experimental.
- Only listening can validate quality: whether the image is stable and natural, whether the 150 ms lag or the
  decay in gaps is audible, how the RTF tier sounds on real recordings, and whether the boost cap and peak guard feel right.
