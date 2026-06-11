---
name: xhs-voice-comment
description: Download Xiaohongshu/XHS voice-comment audio from a share link, copied share text, direct audio URL, or QR-code image, then save it locally as MP3 and WAV. Use when the user provides 小红书语音评论, xhslink.com, Xiaohongshu comment share images, or wants local WAV/MP3 exports from XHS voice messages.
metadata:
  short-description: Export XHS voice comments to local audio files
---

# XHS Voice Comment

Use this skill when the user sends a Xiaohongshu voice-comment share link, copied share text, direct `sns-video-v2.xhscdn.com` audio URL, or a share-card image containing a QR code.

## Workflow

1. Prefer the bundled script. By default it saves both MP3 and WAV and deletes temporary MP4/metadata files:

   ```bash
   scripts/xhs-voice "<share link or image path>" -o xhs_audio_exports
   ```

2. Use `-f mp3` or `-f wav` only when the user explicitly asks for a single format:

   ```bash
   scripts/xhs-voice "<share link or image path>" -f mp3 -o xhs_audio_exports
   scripts/xhs-voice "<share link or image path>" -f wav -o xhs_audio_exports
   ```

3. Filename defaults to the voice comment ASR text. For example, ASR `阿潘阿豚咪走啊` saves as `阿潘阿豚咪走啊.mp3` and `阿潘阿豚咪走啊.wav`. Use `--name-source metadata` only when ASR-based names are not desired.

4. If multiple audio candidates are found, read the candidate list printed by the script. Use `--index N`, `--match TEXT`, or `--all` only when needed.

5. Return only the saved `.mp3` and `.wav` local paths to the user. Use `--write-manifest` only when they need metadata such as ASR text, duration, comment id, or source URL.

6. For share links containing `anchorCommentId`, keep the default `--precision auto`. It uses Xiaohongshu's H5 signed comment pagination through Playwright to locate the exact shared comment before downloading.

## What The Script Does

- Resolves `xhslink.com` short links to the H5 note page.
- Parses `window.__INITIAL_STATE__` from the Xiaohongshu landing page.
- Extracts `audioInfo.playInfo.url` values from comment data.
- Uses `anchorCommentId` and signed H5 comment pagination to locate hidden/paginated voice comments when Playwright is available.
- Names output files from ASR/comment text by default, with metadata fallback.
- Uses the original Xiaohongshu MP4 audio container as a temporary conversion source, then deletes it by default.
- Converts to MP3 and WAV by default.

## Dependencies And Fallbacks

- The skill package is intentionally lightweight. `scripts/xhs-voice` creates a local `.venv` that can reuse system-installed packages, then installs missing capabilities only when the current task needs them.
- Plain copied links and direct `sns-video` URLs usually need only Python 3.
- QR-code image inputs first try existing `cv2` or `zbarimg`; if neither is available, the wrapper installs `opencv-python-headless` into the skill-local `.venv`.
- Exact `anchorCommentId` lookup first tries existing Playwright; if missing, the wrapper installs Playwright and Chromium into the skill-local `.venv`.
- WAV conversion on macOS first tries system `afconvert`; MP3/WAV conversion otherwise tries system `ffmpeg`; if missing, the wrapper installs `imageio-ffmpeg` into the skill-local `.venv`.
- If exact lookup cannot be set up or the target audio is missing, the script stops instead of downloading the wrong audio. Use `--fallback-visible` only when the user explicitly wants visible candidates.
- If the target audio is missing entirely, ask for the copied link to the voice comment, a direct `sns-video` URL, or a logged-in web flow.

## Capability Setup Contract

- Do not ask the user to install dependencies manually unless auto setup fails.
- Use `scripts/bootstrap_local.py --feature <name>` only for explicit troubleshooting. Features are `qr`, `precision`, `convert`, and `tls`.
- Repeated runs should reuse existing system tools or the skill-local `.venv`; do not reinstall capabilities that already pass validation.
- Set `XHS_VOICE_NO_AUTO_SETUP=1` only when the user explicitly wants dependency installation disabled.

## Notes

- Do not ask the user for Xiaohongshu cookies by default.
- Use cookies only when the user explicitly provides them or asks to handle hidden/paginated comments that are unavailable from the public H5 payload.
- Respect platform terms and user privacy; only process links or images the user provides.
