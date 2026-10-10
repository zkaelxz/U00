"""job_process_result.ResultChannel: a large final result travels as a file."""
import os
import pickle
import queue

import pytest

import job_process_result as jpr


@pytest.fixture
def channel(tmp_path):
    return jpr.ResultChannel(queue.Queue(), str(tmp_path / "tmp"), "job")


def test_a_small_result_stays_on_the_queue(channel):
    channel.put(("ok", {"v": 1}))
    assert channel._queue.qsize() == 1
    assert channel._queue.queue[0] == ("ok", {"v": 1})
    assert channel.get(timeout=1) == ("ok", {"v": 1})
    assert not os.path.exists(channel._dir)


def test_a_large_result_is_sent_as_a_file_and_a_short_marker(channel):
    big = {"payload": "x" * (jpr.INLINE_LIMIT_BYTES * 3)}
    channel.put(("ok", big))
    marker = channel._queue.queue[0]
    assert len(pickle.dumps(marker)) < 100
    assert os.path.exists(channel._path)
    assert channel.get(timeout=1) == ("ok", big)


def test_close_removes_the_result_file_and_a_partial_one(channel):
    channel.put(("ok", {"payload": "x" * (jpr.INLINE_LIMIT_BYTES * 2)}))
    with open(channel._path + ".part", "wb") as f:
        f.write(b"half")
    channel.close()
    assert os.listdir(channel._dir) == []


def test_progress_and_other_items_pass_through(channel):
    channel.put(("progress", 0.5, "half"))
    assert channel.get(timeout=1) == ("progress", 0.5, "half")


def test_an_error_message_is_capped(channel):
    channel.put(("error", "RuntimeError", "e" * 100_000))
    item = channel.get(timeout=1)
    assert item[:2] == ("error", "RuntimeError")
    assert len(item[2]) == jpr.MAX_ERROR_MESSAGE_CHARS


def test_get_times_out_when_nothing_arrived(channel):
    with pytest.raises(queue.Empty):
        channel.get(timeout=0.05)
