"""Which files of a Hugging Face CTranslate2 Whisper repo may be downloaded.

Shared by the app (whisper_models) and tools/ja_whisper_models.py so both refuse
the same listings. faster-whisper loads these files as data, with no
trust_remote_code; anything else in a repo (a script, a pickle, a second .bin) is
code that could run on load, so a listing that holds one is refused outright
rather than filtered.
"""

CT2_ALLOWED = frozenset({
    "config.json", "model.bin", "tokenizer.json", "vocabulary.json", "vocabulary.txt",
    "preprocessor_config.json", "README.md", "LICENSE", ".gitattributes",
    "added_tokens.json", "generation_config.json", "normalizer.json",
    "special_tokens_map.json", "tokenizer_config.json",
})
CT2_REQUIRED = ("config.json", "model.bin", "tokenizer.json", "preprocessor_config.json")
# Harmless text a repo always carries; allowed in a listing but not worth fetching.
# The Hugging Face tokenizer/generation JSON files are inert data that faster-whisper
# never reads (it loads config, tokenizer.json, vocabulary and preprocessor_config).
CT2_NOT_FETCHED = frozenset({
    "README.md", "LICENSE", ".gitattributes",
    "added_tokens.json", "generation_config.json", "normalizer.json",
    "special_tokens_map.json", "tokenizer_config.json",
})
_VOCABULARY = ("vocabulary.json", "vocabulary.txt")


class RepoFilesRefused(ValueError):
    """The listing holds something outside the CTranslate2 set, or lacks a file the
    model needs. The message names one file and is safe to show a user."""


def refuse_unexpected_files(names, allowed=CT2_ALLOWED, plain=False):
    """`plain` keeps only a short last path part in the message: a repo chooses its own
    file names, and the app shows this text to a user."""
    for name in names:
        if name not in allowed:
            shown = name.replace("\\", "/").rsplit("/", 1)[-1][:80] if plain else name
            raise RepoFilesRefused(f"the repository contains '{shown}', which is not on the allowlist")


def select_ct2_files(names) -> list:
    """The files to download for a listing, or RepoFilesRefused. Exact names only, so
    'sub/model.bin' and 'pytorch_model.bin' are refused like any other stranger."""
    names = list(names)
    refuse_unexpected_files(names, plain=True)
    present = set(names)
    for required in CT2_REQUIRED:
        if required not in present:
            raise RepoFilesRefused(f"the repository lacks '{required}'")
    if not present & set(_VOCABULARY):
        raise RepoFilesRefused("the repository lacks 'vocabulary.json' or 'vocabulary.txt'")
    return sorted(present - CT2_NOT_FETCHED)
