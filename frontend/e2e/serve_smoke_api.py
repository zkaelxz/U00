"""Launcher for the release smoke test (smoke/release-smoke.spec.ts): the real app (`api.server.create_app`) serving the built
frontend from one port, on an empty throwaway library, with the speech model and translation engine replaced by fakes."""

import os
import queue
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db  # noqa: E402

SMOKE_SEGMENTS = [
    {"start": 0.5, "end": 2.5, "text": "你好，欢迎回来。"},
    {"start": 3.0, "end": 5.0, "text": "今天天气很好。"},
    {"start": 5.5, "end": 7.5, "text": "我们一起去散步吧。"},
]


def install_fake_transcription():
    """Runs the real worker and the real result-applying code in a thread with only the Whisper decode replaced, because a
    spawned child process would not inherit the patch and CI has no speech model."""
    from jobs import job_launch
    from services import transcribe_pipeline

    def fake_whisper(self):
        self.segments = [dict(s) for s in SMOKE_SEGMENTS]
        return None

    transcribe_pipeline._PipelineRun._transcribe_with_whisper = fake_whisper

    def start_in_thread(job_id, target, args=(), on_done=None, on_finish=None, description=None, line_fields=None, **_ignored):
        def run():
            q = queue.Queue()
            try:
                target(*args, q)
                items = []
                while not q.empty():
                    items.append(q.get())
                final = items[-1]
                if final[0] != "ok":
                    raise RuntimeError(f"fake transcription failed: {final[1:]}")
                return on_done(job_id, final[1]) if on_done else final[1]
            finally:
                if on_finish:
                    on_finish(job_id)
        return job_launch.start_job(job_id, run, description=description, line_fields=line_fields)

    job_launch.start_process_job = start_in_thread


def main():
    import uvicorn
    import serve_seeded_api as seeded
    from api.api_config import ApiSettings
    from api.server import create_app
    from tests import fake_engine

    seeded.install_e2e_stubs()
    fake_engine.install()
    install_fake_transcription()
    from services import event_stream_service
    event_stream_service.MAX_STREAMS = 0
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8621
    library_dir = os.path.join(ROOT, "frontend", "test-results", "smoke-library")
    shutil.rmtree(library_dir, ignore_errors=True)
    os.makedirs(library_dir)
    db.configure_library_dir(library_dir)
    app = create_app(ApiSettings(port=port), frontend_dist=os.path.join(ROOT, "frontend", "dist"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
