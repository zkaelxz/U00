"""api/schemas/android_app.py -- The Android shell's update check.
"""

from typing import Optional

from pydantic import BaseModel, Field

__all__ = ["AndroidAppVersion", "AndroidAppFetchResult"]


class AndroidAppVersion(BaseModel):
    version_code: Optional[int] = Field(
        None, description="versionCode of the app build this server offers; null when it holds none.")


class AndroidAppFetchResult(BaseModel):
    version_code: int = Field(description="versionCode of the build the server offers after the fetch.")
    changed: bool = Field(description="False when the server already held this build or a newer one.")
