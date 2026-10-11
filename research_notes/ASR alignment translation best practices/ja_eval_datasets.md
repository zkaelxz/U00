# Japanese evaluation data for anime / game and ASMR / whispered speech

Researched 2026-10-11 by a read-only researcher; saved by the lead session from its report (the researcher's own file was not on disk afterwards). Hugging Face was reachable from the sandbox without credentials; GitHub search was blocked, so a GitHub-only project could have been missed. Not legal advice.

## Headline

- No corpus called "JASMR" could be found (Hugging Face has none by that name; web searches return unrelated results). Ask where the name was seen. Closest real items: `OmniAICreator/ASMR-Archive-Processed`, its SFW subset `noxwano/ASMR-Archive-Processed-SFW`, and `OOPPEENN/ASMR_Dataset`.
- Every Japanese ASMR dataset found has machine-made transcripts, so there is no ready ground truth for ASMR or whispered speech. Machine labels are not acceptable as ground truth.
- For anime / game dialogue there is one practical source; its references are game scripts, which still need a human listen.

## (a) Anime / game dialogue

| Source | Details | Use |
|---|---|---|
| `litagin/Galgame_Speech_ASR_16kHz` | 3.75M clips, 5,354 h, 16 kHz mono OGG, not gated. References = the game script performed by professional voice actors, normalised for ASR (not a checked word-for-word transcript: ad-libs, onomatopoeia, collapsed "..." possible). Licence contradictory: card says GPL-3.0, no commercial use, models trained on it must be open-sourced; the gated upstream says MIT. Audio is commercial game content. NSFW mixed in (2 of the first 5 sampled lines). | Best source. Use only the last shard (`...-000114.tar`, 10,768 clips): anime-whisper was trained on all other shards, so clips from them would flatter it. About 23 KB per clip, so 20 clips are about 0.5 MB read from the start of the shard. |
| JVNV | 1,615 clips, 48 kHz, CC BY-SA 4.0, human scripts, clean emotional speech with laughter and sobbing | Optional extra; the only source here whose audio can be redistributed (attribution and share-alike). |
| `joujiboi/japanese-anime-speech-v2` | 450 h, MP3, licence "gpl" but also "commercial OK"; card does not say whether text is script or ASR; no test split | Weaker alternative. |

## (b) ASMR / whispered speech

| Source | Details | Verdict |
|---|---|---|
| ASMR-Archive-Processed (both versions) | anime-whisper transcribed, then gemini-2.5-flash "refined". AGPL-3.0; uploaders rely on Article 30-4. SFW subset: 44.1 kHz 24-bit stereo FLAC, vocals separated and loudness normalised (easier than real ASMR), about 0.94 MB per clip | Not usable as ground truth for anime-whisper (would measure agreement with itself). |
| `OOPPEENN/ASMR_Dataset` | GPL-3.0, single 383 MB .7z; text from subtitle files shipped with the works; language (Japanese or Chinese) and author unverified (archive over the 50 MB cap) | Unverified. |
| JVS corpus `whisper10` subset | 100 professional speakers x 10 whispered read sentences, 24 kHz, 3.5 GB from Google Drive, audio redistribution not allowed | Only reliable whispered references, but clean studio speech, not ASMR-like. |
| `urcate/japanese_asmr`, `wangdezihao/japanese_asmr` | about 260 GB, no README, appear to be raw dumps without transcripts (not confirmed by download) | No references. |
| ASMR-WS (Frontiers in Neuroscience 2023) | built for language identification; no transcripts or download link found | No. |

## Recommended way to assemble a test set (under 40 MB, about 60-80 clips; guess 2-4 hours of human work)

1. Take 30-40 clips from Galgame shard 114; screen out NSFW.
2. A Japanese speaker corrects each script line to what was actually said and drops clips that cannot be judged.
3. Optionally add about 10 JVNV clips.
4. Add 10-20 JVS `whisper10` clips, using the read sentence as the reference.
5. Add 20-30 ASMR clips, ideally cut from the owner's own raw ASMR files so real background sounds stay in. A human transcribes every one; machine labels only as a draft.
6. Pick one text normalisation (punctuation, character width, "..." and "ー" runs) and apply it to the references and every model's output before computing CER.
7. Keep the audio and a per-clip provenance list outside git.

## Legal reading (not legal advice)

- Personal local benchmarking is the lowest-risk use (the kind of non-enjoyment analysis Article 30-4 covers).
- Do not commit, ship or publish galgame, ASMR or JVS audio. JVNV is the exception (attribution, share-alike). Publishing CER numbers and clip ids is fine.
- The galgame "no commercial use" terms rule out fine-tuning a commercial Baihe build on that data.

## Could not verify

- How often galgame scripts differ from the actual performance (nobody has measured it).
- The language of the OOPPEENN ASMR subtitles.
- Whether the upstream galgame licence is MIT or GPL (card behind a login).
- How ReazonSpeech transcripts were made (not needed for the recommendation).
