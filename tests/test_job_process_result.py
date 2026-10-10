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


def test_discard_removes_the_result_file_and_a_partial_one(channel):
    channel.put(("ok", {"payload": "x" * (jpr.INLINE_LIMIT_BYTES * 2)}))
    with open(channel._path + ".part", "wb") as f:
        f.write(b"half")
    channel.discard()
    assert os.listdir(channel._dir) == []


def test_a_childs_close_leaves_the_result_file_for_the_parent(tmp_path):
    import multiprocessing as mp
    parent_queue = mp.Queue()
    parent = jpr.ResultChannel(parent_queue, str(tmp_path / "tmp"), "job")
    # The spawned child holds its own channel object over the same path.
    child = jpr.ResultChannel(queue.Queue(), str(tmp_path / "tmp"), "job")
    child._path = parent._path
    big = {"payload": "x" * (jpr.INLINE_LIMIT_BYTES * 3)}
    child.put(("ok", big))
    child.close()
    assert os.path.exists(parent._path)
    parent_queue.put((jpr._FILE_MARKER,))
    assert parent.get(timeout=5) == ("ok", big)
    parent.discard()
    assert not os.path.exists(parent._path)


def test_join_and_cancel_join_thread_pass_through(tmp_path):
    import multiprocessing as mp
    channel = jpr.ResultChannel(mp.Queue(), str(tmp_path), "job")
    channel.cancel_join_thread()
    channel.close()
    channel.join_thread()


def test_progress_and_other_items_pass_through(channel):
    channel.put(("progress", 0.5, "half"))
    assert channel.get(timeout=1) == ("progress", 0.5, "half")


def test_an_error_message_is_capped(channel):
    channel.put(("error", "RuntimeError", "e" * 100_000))
    item = channel.get(timeout=1)
    assert item[:2] == ("error", "RuntimeError")
    assert len(item[2]) == jpr.MAX_ERROR_MESSAGE_BYTES


def test_a_multibyte_error_is_capped_in_bytes_below_the_inline_limit(channel):
    channel.put(("error", "RuntimeError", "错" * 8000))
    item = channel.get(timeout=1)
    assert len(item[2].encode("utf-8")) <= jpr.MAX_ERROR_MESSAGE_BYTES
    assert len(pickle.dumps(item)) < jpr.INLINE_LIMIT_BYTES


def test_a_key_at_the_cut_boundary_is_redacted_before_truncating(channel):
    key = "sk-" + "a" * 40
    padding = "x" * (jpr.MAX_ERROR_MESSAGE_BYTES - 8) + " "
    channel.put(("error", "RuntimeError", padding + key))
    text = channel.get(timeout=1)[2]
    assert "sk-a" not in text and "aaaa" not in text


def test_a_result_file_failure_carries_no_path(channel, monkeypatch):
    def boom(*a, **k):
        raise OSError(28, "No space left", channel._dir)
    monkeypatch.setattr(jpr.os, "replace", boom)
    with pytest.raises(OSError) as err:
        channel.put(("ok", {"payload": "x" * (jpr.INLINE_LIMIT_BYTES * 2)}))
    assert channel._dir not in str(err.value)


def test_a_missing_result_file_carries_no_path(channel):
    channel._queue.put((jpr._FILE_MARKER,))
    with pytest.raises(OSError) as err:
        channel.get(timeout=1)
    assert channel._dir not in str(err.value)


def test_get_times_out_when_nothing_arrived(channel):
    with pytest.raises(queue.Empty):
        channel.get(timeout=0.05)


def test_get_removes_the_result_file_once_it_has_read_it(channel):
    big = {"payload": "x" * (jpr.INLINE_LIMIT_BYTES * 3)}
    channel.put(("ok", big))
    assert channel.get(timeout=1) == ("ok", big)
    assert os.listdir(channel._dir) == []
