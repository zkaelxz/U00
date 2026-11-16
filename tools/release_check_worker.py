"""Owns release_check's fixed-decoding child: the app's transcription worker with Whisper's temperature pinned at 0."""
import functools


def pin_temperature(cls) -> None:
    """Makes every `cls.transcribe` call decode at temperature 0, whatever the caller passed.

    Whisper's default decode retries a hard stretch at a higher temperature by sampling, so two runs
    of the same audio differ, which leaves no stable baseline for a regression check. Pinning it
    makes the score repeatable. It is patched here, in the check's own child process, because the
    app's default decoding must not change.
    """
    original = cls.transcribe

    @functools.wraps(original)
    def transcribe(self, *args, **kwargs):
        kwargs["temperature"] = 0.0
        return original(self, *args, **kwargs)

    cls.transcribe = transcribe


def fixed_decoding_worker(*args) -> None:
    """Process target: pins the temperature, then runs the app's own transcription worker."""
    import faster_whisper
    from services import transcribe_pipeline

    # The main decode, the batched path and the sparse-stretch re-hear all go through these two.
    pin_temperature(faster_whisper.WhisperModel)
    pin_temperature(faster_whisper.BatchedInferencePipeline)
    transcribe_pipeline._transcribe_worker(*args)


def seeded_decoding_worker(seed, *args) -> None:
    """Process target: keeps Whisper's default decode (sampled retries included) but fixes the sampler's
    seed, then runs the app's own transcription worker. Seeded in the child because the seed is
    process-wide in ctranslate2 and the app's default must not change."""
    import ctranslate2
    from services import transcribe_pipeline

    ctranslate2.set_random_seed(int(seed))
    transcribe_pipeline._transcribe_worker(*args)
