# Baihe Studio

Baihe Studio turns Chinese, Japanese and Korean audio dramas, videos, novels and comics into English. It runs on your own Windows PC and keeps every title in one library.

**Use it only on content you have the right to use**: files you own or were given, text you have licensed, or pages you may download. It does not unlock paid apps or streaming sites.

## What it does

- **Subtitles**: transcribe audio or video with Whisper, translate it, check the lines, and export SRT, VTT, ASS or LRC.
- **Novels**: translate one from a file, a pasted text or a site, and read it side by side with the original, or listen to it read aloud.
- **Comics**: find the speech bubbles, translate them and letter the English onto the page.
- **Live**: caption a live stream as it plays.
- **Browser extension and phone app**: translate the page you are reading, or send a chapter or video to your PC.
- **Other devices**: let people in your household sign in from their own phone or computer (off by default).

Translation uses the engine you choose: Claude, Gemini, DeepSeek, OpenAI, or Gemma hosted by Ollama. These are paid services. Your files and keys stay on your PC; only what is being translated goes to that engine.

The [user guide](docs/user-guide.md) explains every feature.

## Install

Download `BaiheStudio-Setup-<version>.exe` from the project's releases and run it. Then open **Baihe Studio** from the Start menu. It opens at `http://127.0.0.1:8600/`.

Installing from source, or on another system, is in the [setup guide](docs/user-guide-setup.md).

## First run

1. Open **Settings**. Add a key for your translation engine (or sign in to Ollama for hosted Gemma), and set a monthly spending cap under **Spending**.
2. Open **Diagnostics** and install transcription if you want to work with audio or video.
3. In the **Library**, drop a file or paste a link into the Add box and press **Make English subtitles**.

If something doesn't work, **Diagnostics** checks your setup and says what is missing.

## More

- [User guide](docs/user-guide.md): every feature, settings and troubleshooting.
- [Setup guide](docs/user-guide-setup.md): install from source, portable mode, updates and ports.
- [Household access](docs/household-access.md): using the app from other devices.
- [Docs index](docs/README.md): how the code fits together, for contributors. Rules: [`CLAUDE.md`](CLAUDE.md) and [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Licence

All rights reserved. See [`COPYRIGHT.md`](COPYRIGHT.md).
