"""
api/routers/android_app_routes.py -- the update the Android shell offers itself.

    GET /api/app/android        library.read   which build number the server holds
    GET /api/app/android/apk    library.read   that build's APK

Any signed-in member may fetch it, the same people who can open Baihe in the
app. Both answer 404 when the server holds no build, and the APK route never
accepts a path or name from the request.
"""

from fastapi import APIRouter
from fastapi.responses import FileResponse

from api.auth import require_permission
from api.no_store import NO_STORE, no_store_json
from api.schemas import AndroidAppVersion, error_responses
from lib.errors import NotFoundError
from services import android_app_service as svc

router = APIRouter(tags=["app"])


@router.get("/api/app/android", dependencies=[require_permission("library.read")],
            response_model=AndroidAppVersion, summary="The Android app build this server offers")
def version():
    return no_store_json(AndroidAppVersion(version_code=svc.latest_version_code()))


@router.get("/api/app/android/apk", dependencies=[require_permission("library.read")],
            summary="Download the Android app build", responses=error_responses(404))
def apk():
    path = svc.apk_path()
    if path is None or svc.latest_version_code() is None:
        raise NotFoundError("Not found.")
    return FileResponse(path, media_type="application/vnd.android.package-archive",
                        filename=svc.APK_NAME, headers=dict(NO_STORE))
