"""api/schemas/android_app.py -- The Android shell's update check.
"""

from typing import Optional

from pydantic import BaseModel, Field

__all__ = ["AndroidAppVersion"]


class AndroidAppVersion(BaseModel):
    version_code: Optional[int] = Field(
        None, description="versionCode of the app build this server offers; null when it holds none.")
