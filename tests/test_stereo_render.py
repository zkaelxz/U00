import numpy as np
import pytest

import stereo_cues as sc
import stereo_render as render
from tests.stereo_fixtures import SR, in_band_bands, place, speech_like

ITD_S, ILD_DB = 0.4e-3, 6.0


@pytest.fixture(scope="module")
def source():
    left, right = place(speech_like(seed=4), ITD_S, ILD_DB)
    return left, right, sc.track_cues(left, right, SR)


def _summary(stereo):
    cues = sc.estimate_cues(stereo[:, 0], stereo[:, 1], SR)
    rel = cues.reliable
    assert rel.sum() > 30
    return (np.median(cues.itd_s[rel]), np.median(cues.ild_db[rel][:, in_band_bands(cues, 300, 5000)]),
            np.abs(cues.itd_s[rel] - np.median(cues.itd_s[rel])).max())


@pytest.mark.parametrize("tier", ["static", "follow", "rtf"])
def test_round_trip_re_estimates_the_cues_the_render_used(tier, source):
    left, right, cues = source
    mono = speech_like(seed=9)
    out = {"static": lambda: render.render_static(mono, cues),
           "follow": lambda: render.render_follow(mono, cues),
           "rtf": lambda: render.render_rtf(mono, left, right, cues)}[tier]()
    assert out.shape == (mono.size, 2)
    itd, ild, itd_spread = _summary(out)
    assert itd == pytest.approx(ITD_S, abs=0.05e-3)
    assert ild == pytest.approx(ILD_DB, abs=1.0)
    assert itd_spread < 0.05e-3
    # g_L^2 + g_R^2 = 2 keeps the total power of the stereo pair at twice the mono clip's per-channel power.
    assert (out ** 2).sum() / (2 * (mono ** 2).sum()) == pytest.approx(1.0, abs=0.02)


def test_dual_mono_reference_renders_identical_channels():
    ref = speech_like(seed=0)
    cues = sc.track_cues(ref, ref, SR)
    mono = speech_like(seed=9)
    for out in (render.render_static(mono, cues), render.render_follow(mono, cues),
                render.render_rtf(mono, ref, ref, cues)):
        np.testing.assert_allclose(out[:, 0], out[:, 1], atol=1e-9)
        assert np.abs(out).max() > 0.1


def test_block_size_does_not_change_a_gain_only_placement():
    src = speech_like(8.0, seed=4)
    left, right = place(src, 0.0, ILD_DB)
    cues = sc.track_cues(left, right, SR)
    mono = speech_like(8.0, seed=9)
    long_blocks = render.render_rtf(mono, left, right, cues, block_s=10.0)
    short_blocks = render.render_rtf(mono, left, right, cues, block_s=3.0)
    assert np.abs(long_blocks - short_blocks).max() < 1e-4
    assert np.abs(long_blocks).max() > 0.5


def test_block_size_with_a_delay_differs_only_by_estimation_scatter():
    # With a delay, each frame window sees L and R shifted against each other, so the per-block RTF
    # carries ~1e-3 scatter (measured 1.4e-3 on a peak of 1.1); a pure gain has none (test above).
    src = speech_like(8.0, seed=4)
    left, right = place(src, ITD_S, ILD_DB)
    cues = sc.track_cues(left, right, SR)
    mono = speech_like(8.0, seed=9)
    a = render.render_rtf(mono, left, right, cues, block_s=10.0)
    b = render.render_rtf(mono, left, right, cues, block_s=3.0)
    assert np.abs(a - b).max() < 5e-3


def test_rtf_caps_the_left_right_ratio_at_20db_but_follow_does_not():
    left, right = place(speech_like(), 0.0, 30.0)
    cues = sc.track_cues(left, right, SR)
    mono = speech_like(seed=5)
    assert _summary(render.render_rtf(mono, left, right, cues))[1] == pytest.approx(20.0, abs=1.0)
    assert _summary(render.render_follow(mono, cues))[1] == pytest.approx(30.0, abs=1.0)


def test_rtf_level_follow_tracks_the_source_level_step():
    n = 3 * SR
    rng = np.random.default_rng(0)
    spec = np.fft.rfft(rng.standard_normal(n))
    spec[(np.fft.rfftfreq(n, 1 / SR) < 150) | (np.fft.rfftfreq(n, 1 / SR) > 7000)] = 0
    flat = np.fft.irfft(spec, n=n)
    flat /= np.abs(flat).max()
    env = np.zeros(n)
    env[int(0.8 * SR):int(1.9 * SR)] = 1.0
    env[int(1.9 * SR):] = 0.5
    left, right = place(flat * env, 0.0, 0.0)
    cues = sc.track_cues(left, right, SR)

    def step_db(out):
        rms = lambda a, b: 20 * np.log10(np.sqrt(np.mean(out[int(a * SR):int(b * SR)] ** 2)))
        return rms(1.0, 1.5) - rms(2.3, 2.8)

    assert abs(step_db(render.render_rtf(flat, left, right, cues))) < 0.5
    assert 1.5 < step_db(render.render_rtf(flat, left, right, cues, level_follow=True)) < 6.5


def test_cue_tracks_stretch_over_a_clip_of_another_length(source):
    left, right, cues = source
    for seconds in (1.5, 5.0):
        mono = speech_like(seconds, seed=3)
        assert render.render_follow(mono, cues).shape == (mono.size, 2)
        assert render.render_rtf(mono, left, right, cues).shape == (mono.size, 2)


def test_follow_moves_the_image_when_the_cues_move():
    n = 2 * SR
    mono = speech_like(2.0, seed=3)
    base = sc.estimate_cues(*place(speech_like(2.0, seed=4), 0.0, 0.0), SR)
    frames = base.n_frames
    ramp = np.linspace(-0.5e-3, 0.5e-3, frames)
    moving = sc.CueTracks(**{**base.__dict__, "itd_s": ramp, "reliable": np.ones(frames, bool)})
    out = render.render_follow(mono, moving)
    quarter = n // 4
    early = sc.estimate_cues(out[:quarter, 0], out[:quarter, 1], SR)
    late = sc.estimate_cues(out[-quarter:, 0], out[-quarter:, 1], SR)
    assert np.median(early.itd_s[early.reliable]) < -0.2e-3 < 0.2e-3 < np.median(late.itd_s[late.reliable])


def test_empty_and_silent_clips_render_without_nan(source):
    left, right, cues = source
    for mono in (np.zeros(0), np.zeros(3000)):
        for out in (render.render_static(mono, cues), render.render_follow(mono, cues),
                    render.render_rtf(mono, left, right, cues)):
            assert out.shape == (mono.size, 2)
            assert np.isfinite(out).all() and not out.any()


def test_a_line_with_no_reliable_frames_renders_centred():
    steady = np.random.default_rng(3).standard_normal(SR)
    cues = sc.track_cues(steady, np.roll(steady, 5), SR)
    mono = speech_like(1.0, seed=3)
    for out in (render.render_static(mono, cues), render.render_follow(mono, cues),
                render.render_rtf(mono, steady, np.roll(steady, 5), cues)):
        np.testing.assert_allclose(out[:, 0], out[:, 1], atol=1e-6)
