"""A database-stored media filename that points outside the title folder is
treated as no file by every service that joins it onto the folder."""
import os

import pytest

from services import (diarization_service, dub_service, media_export_service,
                      media_upload_service, source_service, speech_coverage_service,
                      timing_check_service, transcribe_service)
from services.service_errors import InvalidInputError


@pytest.fixture
def outside_name(tmp_path):
    secret = tmp_path / "secret.wav"
    secret.write_bytes(b"s")
    return str(secret)


def _drama(isolated_db, **cols):
    did = isolated_db.create_drama(title_en="t")
    isolated_db.update_drama(did, **cols)
    return did, isolated_db.get_drama(did)


@pytest.mark.parametrize("bad", ["../../x.wav", "/etc/passwd", "C:\\x.wav", "\\\\h\\s\\x.wav"])
def test_audio_lookups_refuse_escape(isolated_db, bad):
    did, drama = _drama(isolated_db, audio_filename=bad, source_video_filename=bad)
    assert transcribe_service._drama_audio_path(did, drama) is None
    assert transcribe_service._drama_video_path(did, drama) is None
    assert diarization_service._drama_audio_path(did, drama) is None
    assert timing_check_service._audio_path(did, drama) is None
    assert dub_service._source_audio_path(drama) is None
    assert source_service._audio_available(did, drama) is False
    assert media_upload_service.has_media(drama, did) is False
    with pytest.raises(InvalidInputError):
        media_export_service._source_video(drama, did)


def test_absolute_path_to_real_file_is_refused(isolated_db, outside_name):
    did, drama = _drama(isolated_db, audio_filename=outside_name)
    assert os.path.exists(outside_name)
    assert transcribe_service._drama_audio_path(did, drama) is None
    assert source_service._audio_available(did, drama) is False


def test_normal_name_still_resolves(isolated_db):
    did, drama = _drama(isolated_db)
    path = os.path.join(isolated_db.drama_dir(did), "audio.wav")
    open(path, "wb").write(b"RIFF")
    drama["audio_filename"] = "audio.wav"
    assert transcribe_service._drama_audio_path(did, drama) == path
    assert source_service._audio_available(did, drama) is True
    assert timing_check_service._audio_path(did, drama) == path
