# Japanese ASR evaluation datasets: anime/game dialogue and ASMR/whispered speech (research notes, 2026-10-11)

Scope: public Japanese speech data that could supply a small (20-40 clip per type, 1-30 s) evaluation set with reference transcripts for (a) anime / game / visual-novel dialogue and (b) ASMR / whispered / breathy speech. Primary sources only: Hugging Face (HF) dataset cards and API listings, corpus home pages, the anime-whisper model card. Everything below was checked from this sandbox on 2026-10-11 unless marked "not verified". Nothing was downloaded into the repository. Two small byte-range samples (1 MB and 8 MB) went to the session scratchpad and were deleted afterwards.

Ground rule used throughout: **machine-generated transcripts (Whisper, anime-whisper, LLM-"refined" ASR) are NOT acceptable as ground truth.** At most they are a draft for a human to correct. This matters even more here because the most likely model under test, litagin/anime-whisper, is the model that produced the labels of the biggest ASMR corpus.

## 1. What is "JASMR"?

### Takeaway
I could not find a corpus, paper or repo called "JASMR" (or "J-ASMR" / "Japanese ASMR corpus" under that name) on Hugging Face or the open web. The closest real things are the machine-labelled HF ASMR datasets in section 3. Ask the owner where they saw the name before relying on it.

### Cited Findings
- An HF dataset search for "JASMR" and "jasmr" returns no datasets. An "ASMR" search lists Japanese ASMR repos such as `OOPPEENN/ASMR_Dataset`, `OmniAICreator/ASMR-Archive-Processed`, `noxwano/ASMR-Archive-Processed-SFW`, `urcate/japanese_asmr` and `wangdezihao/japanese_asmr`, but nothing named JASMR — [HF API search](https://huggingface.co/api/datasets?search=asmr&limit=60)
- Web searches for "JASMR" only turn up a mining-reclamation journal abbreviation (Journal of the American Society of Mining and Reclamation) and a last.fm artist "jASMR". Neither is a speech dataset — [JASMR journal PDF](https://www.asrs.us/DOI/Journals/JASMR/2017v6i1/JASMR17010015-Driscoll.pdf); [last.fm jASMR](https://www.last.fm/music/jASMR)
- The only peer-reviewed "ASMR whispered speech" corpus I found that includes Japanese is ASMR-WS: Song, Yang, Parada-Cabaleiro, Jing, Yamamoto and Schuller, "Identifying languages in a novel dataset: ASMR-whispered speech", *Frontiers in Neuroscience* 17:1120311 (2023). It has 38 videos, 10 h 36 min and 7 languages including Japanese. It was built for **language identification**. The abstract says it is "made accessible to the research community", but the publication page gives no transcripts, licence or download URL — [TUM portal](https://portal.fis.tum.de/en/publications/identifying-languages-in-a-novel-dataset-asmr-whispered-speech/); [DOI](https://doi.org/10.3389/fnins.2023.1120311)

### Inferences
- "JASMR" may be the owner's own shorthand for one of the HF Japanese ASMR datasets (most likely `OmniAICreator/ASMR-Archive-Processed` or `OOPPEENN/ASMR_Dataset`), or a private or very new resource. Neither guess is confirmed.
- ASMR-WS is not usable for ASR scoring unless transcripts exist, and I found no evidence that they do.

### Gaps
- Exact identity of "JASMR": not found. GitHub repo search is blocked from this sandbox (the GitHub API only allows repo-scoped calls), so a GitHub-only project could have been missed.
- Whether ASMR-WS has transcripts, a licence, or a reachable download: not verified.

## 2. Anime / game / visual-novel dialogue candidates

### Takeaway
The only large public source of real anime-style acting with **human-authored** text is visual-novel (galgame) audio paired with the game's own script. litagin/Galgame_Speech_ASR_16kHz is the most practical: it is ungated, streamable, and its last shard was held out of anime-whisper's training. The text is the game script, though, not a checked verbatim transcript, and the audio is scraped commercial game content under a contradictory licence.

### Cited Findings

**litagin/Galgame_Speech_ASR_16kHz** — https://huggingface.co/datasets/litagin/Galgame_Speech_ASR_16kHz
- 3,746,131 audio-text pairs, 5,353.9 h, 115 WebDataset tar shards (100.16 GB, about 897 MB each). Audio is 16 kHz, 16-bit, mono OGG. Clips over 30 s were removed. File names are random SHA-256-like hashes with no game grouping — [dataset card](https://huggingface.co/datasets/litagin/Galgame_Speech_ASR_16kHz)
- Derived from `OOPPEENN/Galgame_Dataset`. The text was normalised for ASR: spaces and dashes removed; full-width letters and digits turned half-width; "……" collapsed to "…"; runs of !?♪♡ collapsed; letter repeats of 3 or more cut to 2; only kana, kanji, a-z, 0-9 and `。、!?…♪♡○` kept. Clips with several conflicting transcriptions were dropped — [card, Modifications](https://huggingface.co/datasets/litagin/Galgame_Speech_ASR_16kHz); [normalize_text_and_filter.py](https://huggingface.co/datasets/litagin/Galgame_Speech_ASR_16kHz/blob/main/normalize_text_and_filter.py)
- The author's stated aim was "anime-like speech ... by professional voice actors, with 100% accurate transcriptions". "Benchmarking Japanese ASR models" is listed as a direct use. The card does not describe any human check of audio against text — [card](https://huggingface.co/datasets/litagin/Galgame_Speech_ASR_16kHz)
- Licence: tagged `gpl-3.0`, plus terms copied from the original repo: GPL-3.0, **no commercial use** of the data or of any model trained on it, and trained models **must be open-sourced**. The source is commercial galgames, and the card jokes that commercial users should get permission from "all the providers". It contains NSFW audio and lines, and has more female than male voices — [card](https://huggingface.co/datasets/litagin/Galgame_Speech_ASR_16kHz)
- Held-out test shard: anime-whisper trained on everything except the **last tar** (`...-000114.tar`, 10,768 pairs), which was kept as test data. anime-whisper's published CERs come from a separate private set of 5 games, about 75k files. On those, whisper-large-v3 averaged 16.5 CER, kotoba-whisper-v2.0 18.8, reazonspeech-nemo-v2 23.6 and anime-whisper 13.0 — [anime-whisper card](https://huggingface.co/litagin/anime-whisper); [num_samples_of_tar.csv](https://huggingface.co/datasets/litagin/Galgame_Speech_ASR_16kHz/blob/main/num_samples_of_tar.csv)
- Sandbox check: the HF API and file downloads work without credentials (no token, HTTP 200). Shard 000114 is 309,770,240 bytes. A 1 MB byte-range read of shard 000000 gave 39 OGG clips averaging about 23 KB each. Durations sampled were 2.0-9.4 s, and ffprobe reports `vorbis, 16000 Hz, 1 ch`. **20 clips ≈ 0.5 MB** and 40 clips ≈ 1 MB, taken by reading the first ~1-2 MB of a shard — [file tree](https://huggingface.co/api/datasets/litagin/Galgame_Speech_ASR_16kHz/tree/main/data)
- What the sample looked like: 2 of the first 5 text lines were NSFW or onomatopoeic ("あー…むっ、はぷ、ちゅずず、ん、ぴちゃ"), so clips must be screened by hand (from the scratchpad sample, deleted afterwards).

**OOPPEENN/Galgame_Dataset (upstream)**
- The repo id `OOPPEENN/Galgame_Dataset` no longer resolves without login. The OOPPEENN account now has `OOPPEENN/56697375616C4E6F76656C5F44617461736574`; the hex decodes to "VisualNovel_Dataset". It is **gated (auto-approve, login required)**, last modified 2025-07-05, and its card metadata says `license: mit`. That conflicts with the GPL-3.0 and no-commercial terms quoted by litagin — [HF API](https://huggingface.co/api/datasets/OOPPEENN/56697375616C4E6F76656C5F44617461736574)
- `OOPPEENN/OnlineGame_Dataset` is also gated and its card was unreadable without login — [HF](https://huggingface.co/datasets/OOPPEENN/OnlineGame_Dataset)

**joujiboi/japanese-anime-speech-v2** — https://huggingface.co/datasets/joujiboi/japanese-anime-speech-v2
- 292,637 clips from visual novels, split into `sfw` (271,788 clips, 397.54 h) and `nsfw` (20,849 clips, 52.36 h). Average sfw clip is 5.3 s. Audio is MP3 at 128 kbps, declared as 16 kHz audio features in parquet; the dataset is about 24 GB. Ungated — [card](https://huggingface.co/datasets/joujiboi/japanese-anime-speech-v2)
- The card does **not** say where the text comes from (script or ASR). It says "no formatting or cleaning of the transcriptions has been performed" but also that repeated characters were normalised. The SFW/NSFW split is rule-based and "won't be 100% reliable" — [card](https://huggingface.co/datasets/joujiboi/japanese-anime-speech-v2)
- Licence is tagged `gpl`, yet the card says "openly available for commercial or non-commercial use". The audio is commercial visual-novel content, so the licence is contradictory and the uploader has no visible rights to grant it — [card](https://huggingface.co/datasets/joujiboi/japanese-anime-speech-v2)
- There is no test split, only sfw and nsfw. Parquet shards are 440-545 MB each, so even a 20-clip pull means streaming with `datasets` or range reads on parquet (not tried) — [file tree](https://huggingface.co/api/datasets/joujiboi/japanese-anime-speech-v2/tree/main/data)

**joujiboi/Galgame-VisualNovel-Reupload** — https://huggingface.co/datasets/joujiboi/Galgame-VisualNovel-Reupload
- Licence `unknown`, ungated, about 489 GB, 1,233 files. It has one config per named game (for example `0verflow_Shiny_Days`, `ALcot_Onigokko`), so it is a re-upload of the per-game galgame data. The card has no information on where the transcripts come from — [card](https://huggingface.co/datasets/joujiboi/Galgame-VisualNovel-Reupload); [HF API](https://huggingface.co/api/datasets/joujiboi/Galgame-VisualNovel-Reupload)

**Clean expressive studio corpora (licensed, human-scripted)**
- JVNV (Takamichi lab): Japanese emotional speech with non-verbal vocalisations. 3.94 h, 1,615 utterances, 4 speakers (F1, F2, M1, M2), six emotions; every utterance contains at least one non-verbal vocalisation (laughter, sobbing) with time spans given. WAV, mono, 24-bit, 48 kHz, with transcripts. Licence CC BY-SA 4.0. A ~1.7 GB zip at ss-takashi.sakura.ne.jp — [JVNV page](https://sites.google.com/site/shinnosuketakamichi/research-topics/jvnv_corpus)
- HF mirrors: `asahi417/jvnv-emotional-speech-corpus` (test split of 1,615 rows, about 2.05 GB parquet) — [card](https://huggingface.co/datasets/asahi417/jvnv-emotional-speech-corpus). `litagin/jvnv_corpus_v1_no_nv` (non-verbal sections cut from audio and text, CC BY-SA 4.0; the author says not everything was checked) — [card](https://huggingface.co/datasets/litagin/jvnv_corpus_v1_no_nv)

**ReazonSpeech (context only; not expressive)**
- `reazon-research/reazonspeech`: Japanese terrestrial-TV speech. Configs are tiny (8.5 h, 600 MB), small (100 h), medium (1,000 h), large (5,000 h) and all (35,000 h, 2.3 TB). FLAC at 16 kHz, licence CDLA-Sharing-1.0 with a disclaimer restricting use "SOLELY FOR THE PURPOSE OF JAPANESE COPYRIGHT ACT ARTICLE 30-4". **Gated** (contact info and login required) — [card](https://huggingface.co/datasets/reazon-research/reazonspeech)
- Ungated eval mirrors exist: `japanese-asr/ja_asr.reazonspeech_test`, `ja_asr.jsut_basic5000` and `ja_asr.common_voice_8_0`. The `japanese-asr/whisper_transcriptions.*` repos are Whisper **pseudo-labels** and must not be used as references — [HF API](https://huggingface.co/api/datasets?author=japanese-asr&search=ja_asr)

### Inferences
- Galgame text is the game's **script**, written by people and performed by professional voice actors, so it is far better than ASR pseudo-labels. It is still not a checked verbatim transcript. Ad-libs, dropped or added particles, onomatopoeia spelling, ruby/kanji choices and the ASR normalisation (… collapsing, repeat cutting, dropped 。) all add disagreements that are not model errors. That conclusion comes from the card's normalisation list plus my own reading of the sample; the card does not quantify it.
- Contamination: litagin/anime-whisper was trained on shards 000000-000113, so clips from those shards would flatter it. Only shard 000114 is fair for anime-whisper. Any other model fine-tuned on galgame data (unknown which) could also be contaminated.
- joujiboi v2 and the Reupload repo do not say whether their text is script or ASR, so they are weaker than litagin's set as a reference source.

### Gaps
- No public anime-dialogue set with a **documented human check of audio against text** was found.
- No TV-anime (as opposed to game) dialogue corpus with transcripts was found. Anime audio plus fan subtitles exists informally, but nothing verifiable turned up.
- Exact licence of upstream OOPPEENN VisualNovel_Dataset: MIT in metadata vs GPL-3.0 and no-commercial per litagin. Unresolved; the card is behind a login.

## 3. ASMR / whispered / breathy speech candidates

### Takeaway
No public Japanese ASMR corpus with human-checked transcripts was found. All the large Japanese ASMR sets on HF are machine-labelled, mostly by anime-whisper. The only whispered Japanese with reliable references is JVS `whisper10`: read prompts, 100 professional speakers × 10 utterances, but studio-clean and not ASMR-like.

### Cited Findings

**OmniAICreator/ASMR-Archive-Processed** — https://huggingface.co/datasets/OmniAICreator/ASMR-Archive-Processed
- Source audio comes from `DeliberatorArchiver/asmr-archive-data-01/02` (an archive of Japanese ASMR works). Processing: low-quality filtering; conversion to 44.1 kHz, 24-bit, stereo FLAC; MelBand-Roformer vocal separation (background and effects removed); loudnorm to -23 LUFS; Silero-VAD segmentation — [card](https://huggingface.co/datasets/OmniAICreator/ASMR-Archive-Processed)
- **Transcripts are machine-made**: litagin/anime-whisper, then gemini-2.5-flash "refinement", with edit-distance guards. Licence AGPL-3.0. Data was shuffled and hashed explicitly "aiming to limit the enjoyment of the original copyrighted works under Japanese copyright law" (an Article 30-4 style rationale). Marked WIP; updates suspended 2026-04-03 after hitting HF storage limits — [card](https://huggingface.co/datasets/OmniAICreator/ASMR-Archive-Processed)
- Size seen through the API: 895 tar shards in the first 1,000 listed files, about 781 GB (the listing is capped at 1,000 files, so the total may be larger) — [file tree](https://huggingface.co/api/datasets/OmniAICreator/ASMR-Archive-Processed/tree/main?recursive=true)

**noxwano/ASMR-Archive-Processed-SFW** — https://huggingface.co/datasets/noxwano/ASMR-Archive-Processed-SFW
- The `nsfw:false` subset of the above, inheriting the source works' tags, so it is not exhaustive. Its card says plainly: "The text transcriptions are entirely AI-generated and lack manual verification". AGPL-3.0, "intended strictly for educational and academic research purposes", ungated — [card](https://huggingface.co/datasets/noxwano/ASMR-Archive-Processed-SFW)
- Sandbox check: an 8 MB byte-range read of `2016/data_0000.tar` gave 10 FLAC clips averaging about 0.94 MB, durations 6.5-11.3 s. Each clip's JSON carries `duration_sec`, the `nsfw` flag and `orig_transcription` (pre-LLM). **20 clips ≈ 19 MB**, under the 50 MB cap, readable from the start of one shard with no credentials — [file tree](https://huggingface.co/api/datasets/noxwano/ASMR-Archive-Processed-SFW/tree/main?recursive=true)

**OOPPEENN/ASMR_Dataset** — https://huggingface.co/datasets/OOPPEENN/ASMR_Dataset
- GPL-3.0, ungated, a single 382,706,548-byte `242115.7z`. Card: "crawl progress: 57% 121249/213330". Pipeline: (1) manual cleaning, discarding works with "no Chinese subtitles" or bad timestamps; (2) MelBand-Roformer to remove sound effects, background music and "saliva sound"; (3) parse subtitles and cut audio; (4) pick the louder channel with anime-whisper; (5) index — [card](https://huggingface.co/datasets/OOPPEENN/ASMR_Dataset)
- The text comes from **subtitle files that ship with or accompany the works**, so it was written by people. The card does not say whether the subtitle text is Japanese, Chinese or bilingual, or who wrote it (circle scripts or fan subs). Not verified: the archive is 383 MB, over the 50 MB limit, and a .7z cannot be range-sampled.

**urcate/japanese_asmr and wangdezihao/japanese_asmr**
- Identical file lists: 14 zip files of about 19 GB each (roughly 260 GB), no README and no stated licence. These look like raw audio dumps with no transcripts (not verified) — [urcate tree](https://huggingface.co/api/datasets/urcate/japanese_asmr/tree/main); [wangdezihao tree](https://huggingface.co/api/datasets/wangdezihao/japanese_asmr/tree/main)

**nyuuzyou/asmr** — **English** ASMR, 283,132 clips, 307 h, 24 kHz WAV, transcripts from distil-large-v3.5 (`annotations_creators: machine-generated`). Not usable for Japanese — [card](https://huggingface.co/datasets/nyuuzyou/asmr)

**JVS corpus, whisper subset** — https://sites.google.com/site/shinnosuketakamichi/research-topics/jvs_corpus
- 100 professional speakers (voice actors and actresses), about 30 h in total. Each speaker records 10 whispered utterances (`whisper10`), so there are 1,000 whispered clips with read-prompt text. Audio is 24 kHz, about 3.5 GB zip on Google Drive. Text follows the JSUT licence and tags are CC BY-SA 4.0. Audio is allowed for academic research, non-commercial research and personal use. **Redistribution of the audio is not allowed**, apart from posting about 10 files on a web page or blog. Commercial use requires contacting the authors — [JVS page](https://sites.google.com/site/shinnosuketakamichi/research-topics/jvs_corpus); [JVS paper arXiv:1908.06248](https://arxiv.org/pdf/1908.06248)
- Download through the sandbox not tested: Google Drive, and over 50 MB.

**English reference point (not Japanese)**: `Malfaro43/whisperedAudio-benchmark` pairs normal and whispered versions of the same utterances from Expresso and EARS. Its design (same text, normal vs whisper) is worth copying — [card](https://huggingface.co/datasets/Malfaro43/whisperedAudio-benchmark)

### Inferences
- Benchmarking anime-whisper (or a Whisper fine-tune trained on these sets) against ASMR-Archive labels is circular: it measures agreement with anime-whisper plus Gemini, not accuracy. The labels can only be a draft for a human to correct by listening.
- ASMR-Archive audio is vocal-separated and loudness-normalised. A benchmark built from it is easier than real ASMR, where binaural movement, ear-cleaning and mouth sounds, and low level are all present. That matters if Baihe's pipeline runs on raw files. For a realistic test the owner should cut clips from their own raw ASMR files.
- JVS whisper10 is the only true-reference whispered Japanese found, but it is clean read speech, not breathy role-play at the microphone.

### Gaps
- No Japanese ASMR set with human-verified transcripts found.
- Language and authorship of OOPPEENN/ASMR_Dataset subtitles: not verified.
- Full size of OmniAICreator/ASMR-Archive-Processed: the API listing was truncated.

## 4. Recommendation, assembly steps, legal notes

### Takeaway
(a) Anime/game: take 30-40 clips from the **held-out last shard (000114) of litagin/Galgame_Speech_ASR_16kHz**, using the game script as a draft reference that a human checks clip by clip. Optionally add about 10 JVNV clips for emotional and non-verbal speech under a clean CC BY-SA licence. (b) ASMR/whisper: there is no ready human-checked source. Use **10-20 JVS whisper10 clips** (true references) plus **20-30 clips from noxwano/ASMR-Archive-Processed-SFW or the owner's own raw ASMR files, transcribed by a human from scratch or by correcting the machine draft**. Keep the set local and do not redistribute it.

### Cited Findings
- Galgame shard 000114 is the anime-whisper hold-out and is ungated; 40 clips take about 1 MB of byte-range read — [anime-whisper card](https://huggingface.co/litagin/anime-whisper); measured in sandbox (section 2)
- ASMR-SFW clips are about 0.94 MB each and machine-labelled — [card](https://huggingface.co/datasets/noxwano/ASMR-Archive-Processed-SFW); measured in sandbox (section 3)
- JVS: no audio redistribution; personal and academic use allowed — [JVS page](https://sites.google.com/site/shinnosuketakamichi/research-topics/jvs_corpus). JVNV: CC BY-SA 4.0 — [JVNV page](https://sites.google.com/site/shinnosuketakamichi/research-topics/jvnv_corpus)
- ReazonSpeech and the ASMR-Archive sets rest on the Japanese Copyright Act Article 30-4 (use for data analysis or machine learning, not "enjoyment") — [ReazonSpeech card](https://huggingface.co/datasets/reazon-research/reazonspeech); [ASMR-Archive card](https://huggingface.co/datasets/OmniAICreator/ASMR-Archive-Processed)

### Inferences (how to assemble it; about 25-30 MB in all)
1. Anime set: stream or byte-range read the start of `galgame-speech-asr-16kHz-train-000114.tar` (the first 2 MB is about 80 clips, enough to discard NSFW and very short ones) into a folder **outside the repo**. Keep 30-40 clips of 1-30 s, mixing short interjections, long lines, shouting and crying. Store `id, audio, script_text`.
2. Human check (anime): a Japanese speaker listens to each clip and fixes the script text to what was actually said (ad-libs, dropped particles), and marks clips where the script cannot be judged (moans, onomatopoeia) for exclusion or a separate bucket. Decide one normalisation (for example, strip punctuation, NFKC, unify … and ー runs) and apply it to both the references and every model's output before computing CER.
3. Optional: download JVNV (1.7 GB zip, CC BY-SA) and take about 10 utterances covering laughter and sobbing. References come from the corpus; only a quick check of the non-verbal markup is needed.
4. Whisper set: download JVS (3.5 GB, Google Drive), take 10-20 `whisper10` clips from mixed speakers, and use the prompt text as the reference (spot-check it).
5. ASMR set: range-read the start of one or two `noxwano/ASMR-Archive-Processed-SFW` shards (20 clips ≈ 19 MB), **or better, cut 20-30 segments from the owner's own legally bought raw ASMR works**, which keep the real binaural sound and effects. Pick whisper-heavy, close-mic and breathy segments. A human must transcribe every clip. For ASMR-Archive clips the stored `orig_transcription` / `.txt` is an anime-whisper/Gemini draft only. Re-listen to the full clip, and watch especially for segment edges cut mid-word by VAD.
6. Record provenance per clip (source repo, shard, uid, licence) in a local manifest, and keep audio and manifest out of git.
- Rough size: about 1 MB (galgame) + about 15 MB (10 JVNV at 48 kHz/24-bit) + about 2 MB (JVS) + about 19 MB (ASMR) ≈ **under 40 MB**, about 60-80 clips. Human effort is likely 2-4 hours (my estimate, not sourced).

### Legal / terms (not legal advice)
- **Personal benchmarking by the owner**: running models over a few dozen clips locally to measure CER is the kind of non-enjoyment analysis the uploaders appeal to (Article 30-4), and JVS and JVNV allow personal or research use. The galgame and ASMR audio is still **commercial copyrighted material scraped and re-uploaded without visible rights-holder permission**. The HF licences (GPL, AGPL, MIT, "commercial OK") were attached by uploaders who do not own the audio, so they grant nothing over the underlying works. Personal local evaluation is the lowest-risk use; that is a judgement, not verified legal advice.
- **Redistribution**: do **not** commit these clips to the Baihe repo, ship them in releases, or publish the test set. JVS explicitly forbids audio redistribution beyond about 10 sample files. JVNV (CC BY-SA 4.0) is the only source here whose audio can be redistributed, with attribution and share-alike. Publishing CER numbers and the list of clip ids/hashes is fine. Publishing the audio or the full script text is not.
- Galgame-derived terms add "no commercial use; trained models must be open-sourced". That does not bind a benchmark-only use, but it rules out fine-tuning on these sets for any commercial Baihe build.
- Content: both galgame and ASMR sources mix in NSFW material, and the SFW flags are unreliable per both cards. A human must screen every clip.

### Gaps
- Galgame script vs. actual performance mismatch rate: not quantified anywhere I found.
- Whether OOPPEENN/ASMR_Dataset subtitles are Japanese human transcripts (which would make it the best ASMR source): unverified, because the archive is 383 MB.
- ReazonSpeech transcript method: I could not extract the NLP2023 paper text. It is not needed for this recommendation.
