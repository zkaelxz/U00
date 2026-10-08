"""api/schemas -- the API contract: every request/response shape the
FastAPI routes expose, as Pydantic models, split into domain modules.

These are the only shapes a client (the React frontend, later the browser
extension) may rely on. They are deliberately *not* the SQLite row: internal
columns (stored filenames, last-error JSON, anything that names a path on
disk) are left out or reduced to a boolean, and `custom_tags` is a list instead
of the comma-joined text column, so the database schema can keep evolving
without breaking a client. Adding a field is a compatible change; renaming or
removing one is not -- bump `API_VERSION` when that has to happen.

Every name is re-exported here, so `from api.schemas import X` works for all
of them. `common` holds what several domain modules share; the other modules
import from `common` only.
"""

from api.schemas.common import *  # noqa: F401,F403
from api.schemas.system import *  # noqa: F401,F403
from api.schemas.loaded_models import *  # noqa: F401,F403
from api.schemas.review import *  # noqa: F401,F403
from api.schemas.characters import *  # noqa: F401,F403
from api.schemas.translate import *  # noqa: F401,F403
from api.schemas.language_packs import *  # noqa: F401,F403
from api.schemas.library import *  # noqa: F401,F403
from api.schemas.sources import *  # noqa: F401,F403
from api.schemas.reader import *  # noqa: F401,F403
from api.schemas.voice import *  # noqa: F401,F403
from api.schemas.transcribe import *  # noqa: F401,F403
from api.schemas.spend_history import *  # noqa: F401,F403
