"""Schemas for GET /api/setup/readiness."""

from pydantic import BaseModel

__all__ = ["SetupReadiness"]


class SetupReadiness(BaseModel):
    """What is installed and configured on the PC. Booleans only: no path, version,
    URL or key, so any signed-in member may read it."""
    faster_whisper: bool
    torch: bool
    torchaudio: bool
    uroman: bool
    soundfile: bool
    audio_separator: bool
    demucs: bool
    pyannote: bool
    onnxruntime: bool
    openai: bool
    anthropic: bool
    yt_dlp: bool
    pypdf: bool
    genanki: bool
    ebooklib: bool
    cv2: bool
    jieba: bool
    sudachi: bool
    kiwi: bool
    nagisa: bool
    soynlp: bool
    pytesseract: bool
    paddle_ocr: bool
    transformers_ok: bool
    # Qwen3 ASR, speech detection and forced alignment all load through torch + transformers + soundfile.
    qwen3_asr: bool
    ffmpeg: bool
    ffmpeg_libass: bool
    js_runtime: bool
    tesseract_program: bool
    gpu_found: bool
    # The installed torch is a CUDA build (from its metadata; nothing is imported).
    torch_cuda: bool
    hf_token_configured: bool
    key_claude: bool
    key_deepseek: bool
    key_gemini: bool
    key_openai: bool
    key_groq: bool
    ollama_reachable: bool
    ollama_model_pulled: bool
