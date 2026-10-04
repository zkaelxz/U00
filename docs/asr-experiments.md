# ASR experiments: Qwen3-ASR batching (Step 103) and MOSS-Transcribe-Diarize (Step 104)

Both are **off by default** and switched on in React **Settings > Transcription
experiments** (PC only). Nothing here changes a default. Whether either one
becomes a default is a later decision, made after the real comparison below.

## What was built

| | Step 103: Qwen3-ASR batching | Step 104: MOSS-Transcribe-Diarize |
|---|---|---|
| Setting | `qwen_asr_batch_size`, 1-16, default 1 (1 = the original one-segment-at-a-time run) | `moss_experimental`, default off |
| Applies when | a drama's ASR backend is **Qwen3 ASR** in Whisper-text mode | a drama's ASR backend is **MOSS-Transcribe-Diarize (experimental)** in Whisper-text mode |
| Code | `asr_backend.Qwen3ASRBackend.transcribe(batch_size=...)` | `asr_backend.MossTranscribeDiarizeBackend`, `BACKENDS`/`get_backend` |
| Written against | qwen-asr **0.0.6** (`transcribe(list)` returns one result per input, in order); any other installed version runs one segment per call | OpenMOSS/MOSS-Transcribe-Diarize commit **61bc29c** (package 0.1.0), HF model revision **704aa4a** |
| Safety | results keyed back by segment index; a batch with the wrong result count is redone one segment at a time; timing is always Whisper's | never picked automatically; refused unless the toggle is on and the package is installed; its own speaker labels are kept and pyannote is not chained over them |

### Before turning MOSS on

- **It runs downloaded code.** The model needs `trust_remote_code`: its Python
  files come from the Hugging Face repo `OpenMOSS-Team/MOSS-Transcribe-Diarize`
  and run inside the Baihe server process, with the same rights as the app
  (including access to stored API keys). The revision is pinned
  (`asr_backend.MOSS_HF_REVISION` = `704aa4a9c304e8520be88901e0d1960158ef5b15`),
  so a change on the Hub can't run new code here; moving the pin needs a review
  of the upstream diff.
- **Household members can start it.** The toggle is PC-only, but once it is on,
  anyone with `lines.edit` + `jobs.start` can set a drama to MOSS and run it
  (a multi-GB model download on first use), the same as Qwen3-ASR.
- **It replaces Qwen3.** It is not on PyPI; install it into the app's own
  environment from the tested commit:
  `pip install "git+https://github.com/OpenMOSS/MOSS-Transcribe-Diarize@61bc29cd4120be7b5d3b761b64cd5dff57263642"`.
  That upgrades Transformers to 5.x, which qwen-asr (pinned to 4.57.6) can't
  use, so Qwen3-ASR and Qwen3 forced alignment stop working until you go back
  (`pip install qwen-asr`). Diagnostics lists it (`moss-transcribe-diarize`) but
  doesn't offer a pip install.
- A run can't be stopped part-way (one blocking call); a Stop takes effect
  when it returns, before any line is replaced.

- **Batching needs the tested qwen-asr.** With any version other than 0.0.6
  installed, Qwen3-ASR sends one line at a time whatever the saved batch size;
  the Settings card says which applies.

### Remote-code check (lead security review, 2026-09-30)

Checked what `trust_remote_code` and the package actually load:

- **Hub repo `OpenMOSS-Team/MOSS-Transcribe-Diarize` at `704aa4a9c304e8520be88901e0d1960158ef5b15`:**
  - `config.json` and `processor_config.json` `auto_map` entries all point at
    modules in the same repo (`configuration_…`, `modeling_…`,
    `processing_…_moss_transcribe_diarize.MossTranscribeDiarize*`). There is no
    cross-repo `Org/repo--module.Class` entry.
  - `tokenizer_config.json` uses the built-in `Qwen2Tokenizer`, and
    `preprocessor_config.json` the built-in `WhisperFeatureExtractor`.
  - The three `.py` files import only `torch`, `numpy` and `transformers`
    (Qwen3/Whisper building blocks). None of them calls `from_pretrained`,
    `snapshot_download`, `hf_hub_download` or anything network or subprocess.
  - So everything that runs comes from the pinned revision.
- **Package `moss_transcribe_diarize` at GitHub commit `61bc29c`:**
  - Baihe imports only the top-level package, `inference_utils` and
    `transcript_parser` (with `subtitle`). None of them downloads anything.
  - The package's `app/` subpackage, its own CLI and web app, does call
    `from_pretrained(..., trust_remote_code=True)` with no revision and does
    network I/O (vLLM client). Baihe never imports `app/`.
  - `inference_utils` uses `transformers.audio_utils.load_audio`, which would
    fetch a URL if given one. Baihe only ever passes the drama's local audio
    path.
- **Moving either pin** (`MOSS_HF_REVISION`, or the install commit) needs this
  check redone.

## Step 101 manual check: speaker detection on the GPU

1. Settings: turn on "Use the GPU for transcription".
2. Open a drama with audio > Transcribe > **Speakers** > **Detect speakers only**.
   While it runs, `nvidia-smi -l 1` should show a `python` process using a few
   GB of GPU memory with non-zero GPU-Util.
3. When it finishes, the line under Speakers should read "Last Detect speakers
   run (pyannote) used the GPU." (`diarization_turns.json` has
   `"device": "cuda"`.)
4. Turn "Use the GPU for transcription" off, run **Detect speakers only**
   again, and confirm the line now says it used the CPU.

## How to evaluate (the roadmap's method)

Use the same held-out, already-reviewed clips for every run: narration,
dialogue, music under speech, quiet speech, overlapping speakers. For each clip
record transcription error (CER against the reviewed text) and diarization
error (speaker labels against the reviewed speakers) separately, plus wall-clock
time and peak GPU memory (`nvidia-smi --query-gpu=memory.used --format=csv -l 1`).

- **Step 103:** run Qwen3 ASR with batch size 1, then 4 and 8, on the same
  clips. Batching may be adopted only if no clip's text changes for the worse.
- **Step 104:** run Whisper + speaker detection, then MOSS, on the same clips.
  Write the go/no-go recommendation here, with the numbers.

## Results

*Not run yet: needs the user's GPU PC.*

## Public benchmark: Japanese (2026-10-04)

Question: which speech-recognition model and settings should Japanese default to? Measured on public, clean read speech with reference transcripts. Results are CPU-only. Nothing here uses drama audio.

### Data
- **FLEURS Japanese** (`google/fleurs`, config `ja_jp`, test split, CC BY 4.0), fetched from the Hugging Face Hub anonymously (no token). The dataset script was not needed: I used the Hub's parquet export `https://huggingface.co/api/datasets/google/fleurs/parquet/ja_jp/test/0.parquet`, revision `refs/convert/parquet` = `168de341b3db6859a9bac1c50a2ef5e3b47647e0` (dataset `main` = `70bb2e84b976b7e960aa89f1c648e09c59f894dd`).
- **First 60 rows** of that file: 784.6 s (13.1 min) of audio, 3,010 reference characters after normalisation. Each clip is one file, transcribed separately, as the app does for one clip. Audio lived in a temp directory, not the repo.
- Reference text is `raw_transcription` (with punctuation), normalised the same way as the output.
- FLEURS `id` is a sentence id and repeats across speakers (60 rows, 55 distinct ids), so each row is identified by row index, id and audio filename:

<details><summary>Utterances (row index, id, audio file)</summary>

```
00 1828 10020345318418093976.wav
01 1834 10086073819084529371.wav
02 1813 10112379500600537933.wav
03 1869 10174984673310355687.wav
04 1744 10193386983118883224.wav
05 1731 10203675650837159328.wav
06 1910 10214476888438240131.wav
07 1771 10228875750894489453.wav
08 1764 10256418415643044307.wav
09 2003 10281646305484957883.wav
10 1980 10300959819649327821.wav
11 1822 1030930262884292674.wav
12 1837 10362598397973084151.wav
13 1736 1036356791308610933.wav
14 1980 10401481013809636276.wav
15 1942 10413033184319592580.wav
16 1733 10440318583992984584.wav
17 1916 10447557607007750141.wav
18 1701 10457758295680448532.wav
19 1848 10460389341605783008.wav
20 1775 10476284249516566719.wav
21 1893 10508563126832413352.wav
22 1908 10595483280529141687.wav
23 1972 10683203810610128933.wav
24 1911 10710420115318518641.wav
25 1664 10728607480570481117.wav
26 1827 10746508960655553792.wav
27 1672 10770212904668726521.wav
28 1861 10772833950120547925.wav
29 1843 10778761596814084242.wav
30 1924 10781591397234148217.wav
31 1958 10930212786007012251.wav
32 1749 1093334804512710473.wav
33 1997 10944803082099949565.wav
34 1825 10969509778910365671.wav
35 1801 10993407044765711136.wav
36 1739 1100824364670689611.wav
37 1977 11038445467160388040.wav
38 1989 11057063102259415865.wav
39 1869 11111871579326016200.wav
40 1667 11129391205542167306.wav
41 1742 11136364513873191636.wav
42 1876 11142098240457961087.wav
43 1943 11160903593055178935.wav
44 1718 11179370060482213220.wav
45 1725 11226361286302510768.wav
46 1690 11241695122085346615.wav
47 1955 11258446014622421491.wav
48 1959 11277998077872294009.wav
49 1721 11308293308024967180.wav
50 1978 11341239153936752827.wav
51 1968 11349680767362432921.wav
52 1718 11354811853117839821.wav
53 1974 11359030312765783730.wav
54 1920 11411683150648548702.wav
55 1733 11424574386275194933.wav
56 1902 11428890487331938751.wav
57 1718 11435168072299453493.wav
58 1750 11489399237032065700.wav
59 1983 11513130081392489537.wav
```
</details>

### Environment
Intel Xeon 2.8 GHz, 4 vCPU, 15 GB RAM, CPU only. Python 3.11.15, faster-whisper 1.2.1, ctranslate2 4.8.2, qwen-asr 0.0.6, torch 2.14.1+cpu, transformers 4.57.6, jiwer 4.0.0. Models were downloaded from the Hub on 2026-10-04 and are not pinned to a revision (Systran `faster-whisper-*` for Whisper, `Qwen/Qwen3-ASR-1.7B`). Whisper ran as the app runs it on a CPU: int8. Qwen ran in bfloat16 (the `load_qwen3_asr` default), `batch_size=1`, language ja, `use_gpu=False`. Memory was never a constraint.

### App defaults (recorded before varying anything)
Read from `services/transcribe_service.py` and `core.transcribe_for_timing`:
- Model: `large-v3-turbo` with the GPU on, `medium` on a CPU (`default_whisper_size`).
- `beam_size` 5, VAD on (Silero) with `min_silence_duration_ms` 300 and `threshold` 0.5, word timestamps on, `condition_on_previous_text` False, `no_repeat_ngram_size` 3, `repetition_penalty` 1.1 (`WHISPER_ANTI_LOOP_KWARGS`), faster-whisper's default temperature fallback list, no initial_prompt unless the drama has glossary terms (none here), then `filter_hallucinated_segments`.

### Method
- Whisper rows call `core.load_whisper_model` and `core.transcribe_for_timing` unchanged. Settings that function does not expose (VAD off, `condition_on_previous_text`, temperature) are applied by wrapping `model.transcribe` to override those keyword arguments; `beam_size` and `initial_prompt` go through the function's own arguments. One setting changes per row, against the app defaults above.
- The Japanese prompt is generic (`これは日本語の文章を読み上げた音声です。`). FLEURS has no proper nouns to prime, so this does not test the glossary-name case the prompt exists for.
- "Qwen3-ASR plain" is `load_qwen3_asr(...).transcribe(audio=clip, language="Japanese")` on the whole clip, with no segmentation. "App path" is `asr_backend.Qwen3ASRVadBackend(model_size="1.7B").transcribe(clip, "ja", use_gpu=False, batch_size=1)`, which adds Silero VAD spans, the 15 s cap, `split_long_segments` and `filter_hallucinated_segments`.
- Timing: sequential, one configuration at a time, 4 threads. Model load and one untimed warm-up clip (row 0) are excluded. RTF = wall-clock seconds over the 784.6 s of audio.
- **CER:** `jiwer` 4.0.0 `process_characters` (substitutions + deletions + insertions at character level). Normalisation: NFKC, casefold, drop whitespace and every Unicode punctuation (category P*) character. Kana and kanji are not converted, and numerals are not (so "30" vs "三十" and "80km" vs "80キロメートル" count as errors). Corpus CER = total edits / total reference characters; per-utterance CER is in the harness output.
- **Confidence intervals:** 2,000 bootstrap resamples of the 60 utterances (seed 0), 95% percentile interval on corpus CER. The "vs same-model default" column is a paired bootstrap of the CER difference in percentage points, same resamples for both rows.
- **Hallucination count:** normalised output longer than 3x the normalised reference (or any output when the reference is empty; no reference here is empty). Empty outputs were also counted and were 0 for every row.

### Results

CER is corpus-level, percent. CI is the 95% bootstrap interval. Δ is the paired difference in percentage points vs the same model's app defaults, with its 95% interval (a negative value is better).

| Model + setting | CER % | 95% CI | Δ vs same-model defaults (pp) | Hallucinations | RTF |
|---|---|---|---|---|---|
| Whisper large-v3-turbo, app defaults | 6.61 | 4.9-8.6 | (baseline) | 0 | 0.56 |
| Whisper large-v3-turbo, VAD off | 6.61 | 4.9-8.6 | +0.00 [-0.44, +0.38] | 0 | 0.56 |
| Whisper large-v3-turbo, beam 1 | 6.68 | 5.1-8.4 | +0.07 [-0.70, +0.65] | 0 | 0.52 |
| Whisper large-v3-turbo, condition_on_previous_text on | 6.61 | 4.9-8.6 | +0.00 [+0.00, +0.00] | 0 | 0.58 |
| Whisper large-v3-turbo, no temperature fallback | 6.61 | 4.9-8.6 | +0.00 [+0.00, +0.00] | 0 | 0.58 |
| Whisper large-v3-turbo, Japanese initial_prompt | 6.71 | 4.8-9.3 | +0.10 [-0.75, +1.20] | 0 | 0.58 |
| Whisper medium, app defaults | 8.47 | 6.6-10.7 | (baseline) | 0 | 0.84 |
| Whisper medium, VAD off | 8.80 | 6.9-11.0 | +0.33 [-0.46, +1.29] | 0 | 0.84 |
| Whisper medium, beam 1 | 8.44 | 6.7-10.3 | -0.03 [-1.01, +0.84] | 0 | 0.57 |
| Whisper medium, condition_on_previous_text on | 8.47 | 6.6-10.7 | +0.00 [+0.00, +0.00] | 0 | 0.84 |
| Whisper medium, no temperature fallback | 8.47 | 6.6-10.7 | +0.00 [+0.00, +0.00] | 0 | 0.84 |
| Whisper medium, Japanese initial_prompt | 8.84 | 6.9-11.0 | +0.37 [-0.40, +1.10] | 0 | 0.86 |
| Whisper large-v3, app defaults | 6.54 | 4.8-8.7 | (baseline) | 0 | 1.31 |
| Whisper large-v3, VAD off | 7.64 | 5.2-10.8 | +1.10 [-0.14, +2.75] | 0 | 1.30 |
| Whisper large-v3, beam 1 | 8.04 | 5.3-11.4 | +1.50 [+0.00, +3.55] | 0 | 0.96 |
| Whisper large-v3, condition_on_previous_text on | not run | | | | |
| Whisper large-v3, no temperature fallback | not run | | | | |
| Whisper large-v3, Japanese initial_prompt | 6.25 | 4.5-8.1 | -0.30 [-0.83, +0.17] | 0 | 1.32 |
| Qwen3-ASR 1.7B plain (whole clip, no VAD) | 5.38 | 3.8-7.3 |  | 0 | 1.20 |
| `Qwen3ASRVadBackend` 1.7B (app path) | 6.64 | 4.7-8.9 |  | 0 | 1.07 |

Pairwise, paired bootstrap on CER (pp, 95% interval): Qwen plain vs turbo defaults -1.23 [-3.35, +0.92]; Qwen plain vs large-v3 defaults -1.16 [-3.44, +1.11]; Qwen plain vs the app-path Qwen backend -1.26 [-2.76, -0.07]; large-v3 vs turbo defaults -0.07 [-0.90, +0.79]; medium vs turbo defaults +1.86 [+0.82, +3.07]; app-path Qwen vs turbo defaults +0.03 [-1.67, +1.86].

Skipped: large-v3 with `condition_on_previous_text` on and with temperature fallback off. Both had output identical to the defaults on turbo and medium, and each large-v3 run takes about 17 min. Nothing else in the planned grid was skipped. Vocal separation, `min_silence_duration_ms`, `vad_threshold`, `fast_mode` and the GPU were not varied.

### Reading the numbers
- Only one gap is clearly real: medium is worse than turbo and large-v3 by about 1.9 pp CER (interval excludes zero), and slower than turbo (RTF 0.84 vs 0.56). On this data the CPU default (`medium`) is worse on both accuracy and speed than `large-v3-turbo`.
- large-v3-turbo and large-v3 are indistinguishable (-0.07 pp), while large-v3 is about 2.3x slower. The "weaker on Japanese/Korean" note on `WHISPER_MODELS["large-v3-turbo"]` is not supported by clean speech; it may still hold for harder audio.
- Qwen3-ASR plain has the lowest CER (5.38%) but its interval overlaps the Whisper models and the gap to turbo is not significant. It is about 2x slower than turbo (RTF 1.20). The app-path Qwen backend equals turbo (6.64% vs 6.61%) and is worse than plain Qwen by 1.3 pp (interval just excludes zero). Spot checks of the largest differences are mostly orthography (Arabic vs kanji numerals, `km` vs `キロメートル`, katakana vs kanji spellings), which CER counts as errors; I did not isolate the cause of the plain vs app-path gap.
- For Japanese on this data I would default to `large-v3-turbo`, VAD on, beam 5, no prompt: it matches large-v3 at about 43% of the time. On a CPU I would also pick it over `medium`, subject to the model download (about 1.6 GB vs 1.5 GB) and the larger memory use, neither of which I measured here.
- Settings: none moved CER beyond noise on turbo or medium. `beam_size` 1 cut time by about 7% (turbo) and 32% (medium) at no measurable accuracy cost (intervals about ±0.8 pp), so it is an option for speed, but on large-v3 it was 1.5 pp worse (interval [0.00, +3.55]), so keep 5 there. VAD off made no difference on turbo or medium and was 1.1 pp worse on large-v3 (interval includes zero); keep it on, which also matters for music and silence that this data lacks. The generic prompt was within noise on all three models (-0.30 to +0.37 pp).
- `condition_on_previous_text` and the temperature fallback are untested in practice: every clip fits in one decoding window and the fallback never triggered (identical outputs on turbo and medium), so these rows say nothing. They need long-form audio.
- Hallucinations were 0 in every configuration, expected on clean speech with no silence or music.
- This is clean, single-speaker read speech. It understates drama audio (overlapping speech, music, noise, quiet or emotional delivery, long silences), where VAD, hallucination handling, the anti-loop settings and the prompt are expected to matter far more. Treat these results as a model-ranking sanity check, not a tuning result.
- 60 clips (3,010 characters) is small: intervals on a single configuration are about ±1.5 to ±2 pp, so differences under about 1 pp should not be acted on. Timings come from one run each on a shared 4-vCPU VM.

<details><summary>Harness (<code>run_bench.py</code> and <code>score.py</code>)</summary>

Setup used: `python -m venv --system-site-packages`, `pip install faster-whisper imageio-ffmpeg pyarrow soundfile numpy jiwer`, `pip install torch --index-url https://download.pytorch.org/whl/cpu`, `pip install qwen-asr`. `ffmpeg` was already on PATH. The first 60 parquet rows were written to `wav/NN.wav` and `refs.json` (`idx`, `id`, `file`, `ref`, `dur`). Then `python run_bench.py "<model>|<setting>" ...` for each row, and `python score.py`.

`run_bench.py`:

```python
import json, os, sys, time
sys.path.insert(0, "/home/user/U00")
S = os.path.dirname(os.path.abspath(__file__))
refs = json.load(open(f"{S}/refs.json"))
JA_PROMPT = "これは日本語の文章を読み上げた音声です。"
os.makedirs(f"{S}/results", exist_ok=True)

def whisper_cfg(size, name):
    over = {}      # extra faster-whisper kwargs, applied under the app's own call
    call = dict(beam_size=5, min_silence_duration_ms=300, initial_prompt="")
    if name == "novad": over["vad_filter"] = False
    if name == "beam1": call["beam_size"] = 1
    if name == "condprev_on": over["condition_on_previous_text"] = True
    if name == "temp0": over["temperature"] = 0.0
    if name == "prompt_ja": call["initial_prompt"] = JA_PROMPT
    return over, call

def run_whisper(size, name):
    import core
    model = core.load_whisper_model(size, use_gpu=False)
    over, call = whisper_cfg(size, name)
    orig = model.transcribe
    def patched(audio, **kw):
        kw.update(over)
        return orig(audio, **kw)
    model.transcribe = patched
    def one(i):
        return core.transcribe_for_timing(f"{S}/wav/{i:02d}.wav", size, language="ja", use_gpu=False, **call)
    try:
        one(0)  # warm-up, untimed
        out = []; t0 = time.time()
        for r in refs:
            segs = one(r["idx"]); out.append("".join(s["text"] for s in segs))
        return out, time.time() - t0
    finally:
        model.transcribe = orig

def run_qwen_plain():
    import asr_backend
    model = asr_backend.load_qwen3_asr(use_gpu=False, model_size="1.7B")
    def one(i):
        res = model.transcribe(audio=f"{S}/wav/{i:02d}.wav", language="Japanese")
        return res[0].text if res else ""
    one(0)
    out = []; t0 = time.time()
    for r in refs: out.append(one(r["idx"]))
    return out, time.time() - t0

def run_qwen_vad():
    import asr_backend
    b = asr_backend.Qwen3ASRVadBackend(model_size="1.7B")
    def one(i):
        segs = b.transcribe(f"{S}/wav/{i:02d}.wav", "ja", use_gpu=False, batch_size=1)
        return "".join(s["text"] for s in segs)
    one(0)
    out = []; t0 = time.time()
    for r in refs: out.append(one(r["idx"]))
    return out, time.time() - t0

def main(keys):
    for key in keys:
        path = f"{S}/results/{key}.json"
        if os.path.exists(path): continue
        print(time.strftime("%H:%M:%S"), "start", key, flush=True)
        if key == "qwen_plain": hyp, wall = run_qwen_plain()
        elif key == "qwen_vad": hyp, wall = run_qwen_vad()
        else:
            size, name = key.split("|"); hyp, wall = run_whisper(size, name)
        json.dump({"key": key, "wall": wall, "hyp": hyp}, open(path, "w"), ensure_ascii=False)
        print(time.strftime("%H:%M:%S"), "done", key, round(wall), "s", flush=True)

if __name__ == "__main__":
    main(sys.argv[1:])
```

`score.py`:

```python
import json, glob, os, unicodedata, random, sys
import jiwer
S = os.path.dirname(os.path.abspath(__file__))
refs = json.load(open(f"{S}/refs.json")); TOTAL = sum(r["dur"] for r in refs)

def norm(t):
    t = unicodedata.normalize("NFKC", t or "").casefold()
    return "".join(c for c in t if not c.isspace() and not unicodedata.category(c).startswith("P"))

R = [norm(r["ref"]) for r in refs]

def edits(h, r):
    if not h: return len(r)
    o = jiwer.process_characters(r, h)
    return o.substitutions + o.deletions + o.insertions

def score(hyp):
    H = [norm(h) for h in hyp]
    e = [edits(h, r) for h, r in zip(H, R)]
    n = [len(r) for r in R]
    halluc = sum(1 for h, r in zip(H, R) if (not r and h) or (r and len(h) > 3 * len(r)))
    empty = sum(1 for h in H if not h)
    return e, n, halluc, empty

def cer(e, n, idx): return sum(e[i] for i in idx) / sum(n[i] for i in idx)

def boot(e, n, B=2000, seed=0):
    rng = random.Random(seed); k = len(e); out = []
    for _ in range(B):
        idx = [rng.randrange(k) for _ in range(k)]
        out.append(cer(e, n, idx))
    out.sort(); return out[int(.025 * B)], out[int(.975 * B)]

def paired(e1, e0, n, B=2000, seed=1):
    rng = random.Random(seed); k = len(e1); d = []
    for _ in range(B):
        idx = [rng.randrange(k) for _ in range(k)]
        d.append(cer(e1, n, idx) - cer(e0, n, idx))
    d.sort(); return d[int(.025 * B)], d[int(.975 * B)]

res = {}
for p in sorted(glob.glob(f"{S}/results/*.json")):
    d = json.load(open(p)); e, n, h, em = score(d["hyp"]); res[d["key"]] = (d, e, n, h, em)
out = []
for key, (d, e, n, h, em) in res.items():
    lo, hi = boot(e, n)
    base = key.split("|")[0] + "|default"
    dl = ""
    if "|" in key and not key.endswith("|default") and base in res:
        a, b = paired(e, res[base][1], n); dl = f"{(cer(e,n,range(60))-cer(res[base][1],n,range(60)))*100:+.2f} [{a*100:+.2f}, {b*100:+.2f}]"
    out.append(dict(key=key, cer=cer(e, n, range(60)) * 100, lo=lo * 100, hi=hi * 100, halluc=h, empty=em,
                    wall=d["wall"], rtf=d["wall"] / TOTAL, delta=dl, utt_cer=[ (e[i]/n[i]) for i in range(60)]))
json.dump(out, open(f"{S}/summary.json", "w"), ensure_ascii=False, indent=1)
print(f"{'config':32} {'CER%':>6} {'95% CI':>14} {'hall':>4} {'empty':>5} {'wall_s':>7} {'RTF':>5}  delta vs default (pp, paired CI)")
for o in out:
    print(f"{o['key']:32} {o['cer']:6.2f} [{o['lo']:5.2f},{o['hi']:5.2f}] {o['halluc']:4d} {o['empty']:5d} {o['wall']:7.0f} {o['rtf']:5.2f}  {o['delta']}")
```
</details>
