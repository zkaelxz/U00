"""services/voices_service.py -- the Voices screen: every enrolled voice print across series, how well each linked speaker matches it, and clearing, retraining or trimming a print while the person stays.

A print is one running average plus a sample count (db.speakers); individual samples are not stored. What can be
shown and redone comes from the speakers linked to the person: their embeddings stay in each title's speaker
detection file, so a print is rebuilt from those, never by subtracting a remembered sample. Samples whose title
or detection file is gone cannot be rebuilt and drop out of a retrain, which the UI says before it asks.

No FastAPI import.
"""
import json

import db
import voice_id
from lib import ids as lib_ids
from lib.errors import ConflictError, InvalidInputError, NotFoundError
from services import ownership_service


def _require_confirm(confirm, what: str):
    if confirm is not True:
        raise InvalidInputError(f"{what} needs confirm=true.")


def _title(link: dict) -> str:
    return link.get("title_en") or link.get("title_zh") or f"Title {link['drama_id']}"


def _stored_embedding(drama_id: int, speaker_label: str):
    try:
        import diarize
        embeddings = diarize.load_embeddings(db.drama_dir(drama_id))
    except (ImportError, OSError, ValueError):
        return None
    vec = embeddings.get(speaker_label) if isinstance(embeddings, dict) else None
    if not (isinstance(vec, list) and vec):
        # Renamed or merged speakers: their embedding is still under the detector's label.
        vec = diarize.embedding_from_lines(db.drama_dir(drama_id), speaker_label,
                                           db.load_line_objects(drama_id))
    return vec if isinstance(vec, list) and vec else None


def _print(sc: dict):
    try:
        vec = json.loads(sc["voice_fingerprint"]) if sc.get("voice_fingerprint") else None
    except (TypeError, ValueError):
        return None
    return vec if isinstance(vec, list) and vec else None


def _samples(sc: dict, principal) -> list:
    """The linked speakers the principal may see, each with its similarity to the print (None when no
    embedding is stored for it)."""
    fingerprint = _print(sc)
    out = []
    for link in db.list_series_character_links(sc["id"]):
        if not ownership_service.can_see_drama(principal, link["drama_id"]):
            continue
        vec = _stored_embedding(link["drama_id"], link["speaker_label"])
        similarity = None
        if vec is not None and fingerprint is not None:
            similarity = round(voice_id.cosine_similarity(vec, fingerprint), 4)
        out.append({"drama_id": link["drama_id"], "title": _title(link),
                    "speaker_label": link["speaker_label"], "similarity": similarity})
    return out


def _voice(series: dict, sc: dict, principal) -> dict:
    samples = _samples(sc, principal)
    scored = [s["similarity"] for s in samples if s["similarity"] is not None]
    return {
        "series_id": series["id"], "series_name": series["name"],
        "character_id": sc["id"], "character_name": sc["character_name"],
        "sample_count": sc.get("voice_fingerprint_samples") or 0,
        "linked": samples,
        "average_similarity": round(sum(scored) / len(scored), 4) if scored else None,
        "lowest_similarity": min(scored) if scored else None,
        "rebuildable": len(scored),
    }


def list_voices(principal=None) -> list:
    """Every person with a voice print in a series the principal may see, series by series."""
    out = []
    for series in db.list_series(visible_to=ownership_service.visible_to_filter(principal)):
        for sc in db.list_series_characters(series["id"]):
            if _print(sc) is not None:
                out.append(_voice(series, sc, principal))
    return out


def _require_enrolled(series_id, character_id) -> dict:
    lib_ids.check_id_of("series", series_id)
    lib_ids.check_id_of("series character", character_id)
    sc = next((c for c in db.list_series_characters(series_id) if c["id"] == character_id), None)
    if sc is None:
        raise NotFoundError(f"No character {character_id} in series {series_id}.")
    if _print(sc) is None:
        raise ConflictError("This person has no voice print.")
    return sc


def _rebuilt(sc: dict, skip: tuple | None = None):
    """(mean embedding, count) from the linked speakers' stored embeddings, minus `skip` ((drama_id,
    speaker_label)). Embeddings of a different length than the most common one come from another model
    and are left out rather than averaged in. (None, 0) when nothing is left."""
    vectors = []
    for link in db.list_series_character_links(sc["id"]):
        if skip == (link["drama_id"], link["speaker_label"]):
            continue
        vec = _stored_embedding(link["drama_id"], link["speaker_label"])
        if vec is not None:
            vectors.append(vec)
    if not vectors:
        return None, 0
    lengths = [len(v) for v in vectors]
    size = max(set(lengths), key=lengths.count)
    vectors = [v for v in vectors if len(v) == size]
    mean = [sum(col) / len(vectors) for col in zip(*vectors)]
    return mean, len(vectors)


def clear_voice(series_id: int, character_id: int, confirm=False) -> dict:
    """Forgets the voice print; the person, their aliases, notes and links to titles stay."""
    sc = _require_enrolled(series_id, character_id)
    _require_confirm(confirm, "Clearing a voice print")
    db.set_series_character_voice_fingerprint(sc["id"], None, 0)
    return {"series_id": series_id, "character_id": character_id, "sample_count": 0}


def retrain_voice(series_id: int, character_id: int, confirm=False) -> dict:
    """Rebuilds the print from the speakers now linked to this person. ConflictError when none has a
    stored embedding, since that would erase the print (Clear does that on purpose)."""
    sc = _require_enrolled(series_id, character_id)
    _require_confirm(confirm, "Retraining a voice print")
    mean, count = _rebuilt(sc)
    if mean is None:
        raise ConflictError("None of the linked speakers has a stored voice to rebuild from. "
                            "Clear the voice print instead.")
    db.set_series_character_voice_fingerprint(sc["id"], mean, count)
    return {"series_id": series_id, "character_id": character_id, "sample_count": count}


def remove_sample(series_id: int, character_id: int, drama_id: int, speaker_label: str,
                  confirm=False) -> dict:
    """Takes one wrongly accepted speaker out: it is unlinked (it keeps its name) and the print is rebuilt
    from the rest, or cleared when nothing else is left."""
    sc = _require_enrolled(series_id, character_id)
    lib_ids.check_id_of("drama", drama_id)
    if not isinstance(speaker_label, str) or not speaker_label:
        raise InvalidInputError("speaker_label is missing.")
    _require_confirm(confirm, "Removing a voice sample")
    if not any(l["drama_id"] == drama_id and l["speaker_label"] == speaker_label
               for l in db.list_series_character_links(sc["id"])):
        raise NotFoundError("That speaker is not linked to this person.")
    mean, count = _rebuilt(sc, skip=(drama_id, speaker_label))
    if not db.set_series_character_voice_fingerprint(sc["id"], mean, count,
                                                     unlink=(drama_id, speaker_label)):
        raise ConflictError("That speaker was changed meanwhile; reload and try again.")
    return {"series_id": series_id, "character_id": character_id, "sample_count": count}
