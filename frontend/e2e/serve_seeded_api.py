"""
Test-only launcher for the end-to-end check (frontend/e2e/library.spec.ts):
starts the real FastAPI app against a throwaway library seeded with a few
known dramas, so the browser test exercises React -> FastAPI -> the real
service layer and db.py, without ever touching a person's actual library.

Usage (Playwright's webServer runs this for you):
    python frontend/e2e/serve_seeded_api.py [port]
"""

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import db  # noqa: E402


def seed(library_dir: str):
    db.configure_library_dir(library_dir)
    db.create_drama(title_zh="魔道祖师", title_en="Grandmaster of Demonic Cultivation",
                    author="墨香铜臭", author_romanized="Mo Xiang Tong Xiu",
                    status="translated", media_type="audio_drama", source_language="zh",
                    custom_tags="Favorite, wuxia", summary="Seeded for the e2e test.")
    db.create_drama(title_zh="天官赐福", title_en="Heaven Official's Blessing",
                    status="aligned", media_type="novel", source_language="zh",
                    custom_tags="Plan to Translate")
    db.create_drama(title_zh="시그널", title_en="Signal", status="not started",
                    media_type="video_drama", source_language="ko")


def main():
    import uvicorn
    from api.api_config import ApiSettings
    from api.server import create_app

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8611
    # A fixed, gitignored folder wiped at every start, rather than a temp
    # dir cleaned up on exit: Playwright may kill this process outright,
    # and a `finally` never runs then.
    library_dir = os.path.join(ROOT, "frontend", "test-results", "e2e-library")
    shutil.rmtree(library_dir, ignore_errors=True)
    os.makedirs(library_dir)
    seed(library_dir)
    uvicorn.run(create_app(ApiSettings(port=port)), host="127.0.0.1", port=port,
                log_level="warning")


if __name__ == "__main__":
    main()
