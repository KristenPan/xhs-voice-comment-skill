#!/usr/bin/env python3
"""
Download voice-comment audio from a Xiaohongshu share link or QR-code image.

The script intentionally keeps hard dependencies near zero:
- stdlib handles HTTP, parsing, download, and metadata.
- OpenCV/zbarimg are optional helpers for QR-code images.
- ffmpeg is optional but required for WAV/MP3 conversion. The source MP4 audio is
  used as a temporary conversion input and deleted by default.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any, Iterable


UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)
STATE_MARKER = "window.__INITIAL_STATE__="
SKILL_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_SCRIPT = SKILL_ROOT / "scripts" / "bootstrap_local.py"
VENV_PYTHON = SKILL_ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


@dataclass
class AudioEntry:
    comment_id: str
    target_comment_id: str
    note_id: str
    audio_id: str
    duration_ms: int
    nickname: str
    user_id: str
    content: str
    asr_text: str
    tag_text: str
    source_url: str

    @property
    def duration_s(self) -> float:
        return round(self.duration_ms / 1000, 3) if self.duration_ms else 0.0


class XHSClient:
    def __init__(self, cookie: str = "") -> None:
        self.cookies = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=default_ssl_context()),
            urllib.request.HTTPCookieProcessor(self.cookies),
        )
        self.insecure_opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl._create_unverified_context()),
            urllib.request.HTTPCookieProcessor(self.cookies),
        )
        self.cookie = cookie.strip()
        self.warned_tls_fallback = False
        self.tried_tls_bootstrap = False

    def request(self, url: str, *, referer: str = "", accept: str = "*/*") -> tuple[bytes, str, str]:
        headers = {
            "User-Agent": UA_MOBILE,
            "Accept": accept,
        }
        if referer:
            headers["Referer"] = referer
        if self.cookie:
            headers["Cookie"] = self.cookie
        req = urllib.request.Request(url, headers=headers)
        opener = self.opener
        try:
            with opener.open(req, timeout=30) as resp:
                data = resp.read()
                return data, resp.geturl(), resp.headers.get("content-type", "")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:600]
            raise RuntimeError(f"HTTP {exc.code} while fetching {url}: {body}") from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, ssl.SSLCertVerificationError) and is_xhs_domain(url):
                if not self.tried_tls_bootstrap and auto_setup_enabled():
                    self.tried_tls_bootstrap = True
                    bootstrap_capability("tls", "trusted certificate bundle for Xiaohongshu HTTPS requests")
                    self.opener = urllib.request.build_opener(
                        urllib.request.HTTPSHandler(context=default_ssl_context()),
                        urllib.request.HTTPCookieProcessor(self.cookies),
                    )
                    with self.opener.open(req, timeout=30) as resp:
                        data = resp.read()
                        return data, resp.geturl(), resp.headers.get("content-type", "")
                if not self.warned_tls_fallback:
                    print("Warning: local Python certificate verification failed; retrying XHS request without TLS verification.", file=sys.stderr)
                    self.warned_tls_fallback = True
                with self.insecure_opener.open(req, timeout=30) as resp:
                    data = resp.read()
                    return data, resp.geturl(), resp.headers.get("content-type", "")
            raise RuntimeError(f"Network error while fetching {url}: {exc}") from exc


def is_xhs_domain(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    return host == "xhslink.com" or host.endswith(".xiaohongshu.com") or host.endswith(".xhscdn.com")


def auto_setup_enabled() -> bool:
    return os.environ.get("XHS_VOICE_NO_AUTO_SETUP", "").lower() not in {"1", "true", "yes"}


def module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def in_skill_venv() -> bool:
    try:
        return Path(sys.executable).resolve() == VENV_PYTHON.resolve()
    except Exception:
        return False


def bootstrap_capability(feature: str, reason: str) -> None:
    if not auto_setup_enabled():
        raise RuntimeError(f"Missing capability {feature}: {reason}. Auto setup is disabled by XHS_VOICE_NO_AUTO_SETUP.")
    if not BOOTSTRAP_SCRIPT.exists():
        raise RuntimeError(f"Missing bootstrap script: {BOOTSTRAP_SCRIPT}")
    print(f"Preparing missing capability: {feature} ({reason})", file=sys.stderr)
    subprocess.run([sys.executable, str(BOOTSTRAP_SCRIPT), "--feature", feature], check=True)
    importlib.invalidate_caches()
    if not in_skill_venv() and VENV_PYTHON.exists():
        os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])


def ensure_module_capability(module_name: str, feature: str, reason: str) -> None:
    if module_available(module_name):
        return
    bootstrap_capability(feature, reason)
    importlib.invalidate_caches()
    if not module_available(module_name):
        raise RuntimeError(f"Capability setup completed, but Python module is still unavailable: {module_name}")


def default_ssl_context() -> ssl.SSLContext:
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def extract_urls_from_text(text: str) -> list[str]:
    urls = []
    for raw in URL_RE.findall(text):
        cleaned = raw.rstrip("。），,)")
        if cleaned not in urls:
            urls.append(cleaned)
    return urls


def decode_qr_with_cv2(path: Path) -> str:
    try:
        import cv2  # type: ignore
    except Exception:
        return ""

    img = cv2.imread(str(path))
    if img is None:
        return ""
    detector = cv2.QRCodeDetector()

    candidates = [("whole", img)]
    h, w = img.shape[:2]
    candidates.extend(
        [
            ("lower_right", img[int(h * 0.45) :, int(w * 0.45) :]),
            ("center", img[int(h * 0.20) : int(h * 0.85), int(w * 0.15) : int(w * 0.90)]),
        ]
    )
    for _, candidate in candidates:
        data, _, _ = detector.detectAndDecode(candidate)
        if data:
            return data.strip()
    return ""


def decode_qr_with_zbar(path: Path) -> str:
    zbar = shutil.which("zbarimg")
    if not zbar:
        return ""
    proc = subprocess.run(
        [zbar, "--quiet", "--raw", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""


def urls_from_input_items(items: list[str]) -> list[str]:
    urls: list[str] = []
    for item in items:
        path = Path(item).expanduser()
        if path.exists():
            if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}:
                decoded = decode_qr_with_cv2(path) or decode_qr_with_zbar(path)
                if not decoded and not module_available("cv2") and not shutil.which("zbarimg"):
                    bootstrap_capability("qr", "QR-code decoding for Xiaohongshu share images")
                    decoded = decode_qr_with_cv2(path) or decode_qr_with_zbar(path)
                if not decoded:
                    raise RuntimeError(
                        f"Could not decode a QR code from {path}. Provide the copied XHS link instead, "
                        "or rerun with auto setup enabled so the skill can install QR decoding support."
                    )
                urls.extend(extract_urls_from_text(decoded))
            else:
                urls.extend(extract_urls_from_text(path.read_text(encoding="utf-8", errors="ignore")))
        else:
            urls.extend(extract_urls_from_text(item))
    unique: list[str] = []
    for url in urls:
        if url not in unique:
            unique.append(url)
    return unique


def sanitize_filename(value: str, fallback: str = "xhs_audio") -> str:
    value = re.sub(r"[\\/:*?\"<>|\n\r\t]+", "_", value).strip(" ._")
    value = re.sub(r"\s+", " ", value)
    return value[:96] or fallback


def filename_stem_for_entry(entry: AudioEntry, *, title: str, name_source: str) -> str:
    if name_source == "asr":
        for value in [entry.asr_text, entry.content]:
            if value.strip():
                return sanitize_filename(value, "xhs_audio")
    stem_bits = [
        sanitize_filename(title, "xhs_note"),
        sanitize_filename(entry.nickname, "user"),
        entry.comment_id or entry.audio_id or str(int(time.time())),
        f"{entry.duration_s:g}s" if entry.duration_s else "",
    ]
    return sanitize_filename("_".join(bit for bit in stem_bits if bit), "xhs_audio")


def js_state_to_json(blob: str) -> dict[str, Any]:
    blob = re.sub(r"(?<=[:\[,])undefined(?=[,}\]])", "null", blob)
    return json.loads(blob)


def extract_initial_state(html: str) -> dict[str, Any]:
    start = html.find(STATE_MARKER)
    if start == -1:
        raise RuntimeError("XHS page did not contain window.__INITIAL_STATE__.")
    start += len(STATE_MARKER)
    end = html.find("</script>", start)
    if end == -1:
        raise RuntimeError("Could not find the end of window.__INITIAL_STATE__.")
    return js_state_to_json(html[start:end])


def get_nested(obj: dict[str, Any], path: Iterable[str], default: Any = None) -> Any:
    cur: Any = obj
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def first_present(obj: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in obj:
            return obj[key]
    return default


def parse_note_context(final_url: str, state: dict[str, Any]) -> dict[str, str]:
    parsed = urllib.parse.urlparse(final_url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    note_id = ""
    match = re.search(r"/(?:discovery/item|explore)/([0-9a-fA-F]+)", parsed.path)
    if match:
        note_id = match.group(1)
    note_id = note_id or str(get_nested(state, ["noteData", "data", "noteData", "noteId"], "") or "")
    route_query = get_nested(state, ["noteData", "routeQuery"], {}) or {}
    return {
        "final_url": final_url,
        "note_id": note_id,
        "anchor_comment_id": str(query.get("anchorCommentId") or route_query.get("anchorCommentId") or ""),
        "xsec_token": str(query.get("xsec_token") or query.get("xsecToken") or route_query.get("xsec_token") or ""),
        "xsec_source": str(query.get("xsec_source") or query.get("xsecSource") or route_query.get("xsec_source") or ""),
        "title": str(get_nested(state, ["noteData", "data", "noteData", "title"], "") or ""),
    }


def walk_audio_entries(obj: Any, entries: list[AudioEntry]) -> None:
    if isinstance(obj, dict):
        audio = first_present(obj, "audioInfo", "audio_info", default=None)
        play = first_present(audio, "playInfo", "play_info", default=None) if isinstance(audio, dict) else None
        source_url = play.get("url") if isinstance(play, dict) else ""
        if isinstance(audio, dict) and source_url:
            target_raw = first_present(obj, "targetComment", "target_comment", default={})
            target = target_raw if isinstance(target_raw, dict) else {}
            user = obj.get("user") if isinstance(obj.get("user"), dict) else {}
            entries.append(
                AudioEntry(
                    comment_id=str(obj.get("id") or ""),
                    target_comment_id=str(target.get("id") or ""),
                    note_id=str(first_present(obj, "noteId", "note_id", default="") or ""),
                    audio_id=str(first_present(audio, "audioId", "audio_id", default="") or ""),
                    duration_ms=int(audio.get("duration") or 0),
                    nickname=str(user.get("nickname") or user.get("nickName") or ""),
                    user_id=str(first_present(user, "userId", "user_id", "id", default="") or ""),
                    content=str(obj.get("content") or ""),
                    asr_text=str(first_present(audio, "asrText", "asr_text", default="") or ""),
                    tag_text=str(first_present(audio, "tagText", "tag_text", default="") or ""),
                    source_url=str(source_url),
                )
            )
        for value in obj.values():
            walk_audio_entries(value, entries)
    elif isinstance(obj, list):
        for value in obj:
            walk_audio_entries(value, entries)


def collect_audio_entries(state: dict[str, Any]) -> list[AudioEntry]:
    entries: list[AudioEntry] = []
    walk_audio_entries(state, entries)
    deduped: list[AudioEntry] = []
    seen: set[str] = set()
    for entry in entries:
        key = entry.audio_id or entry.source_url
        if key and key not in seen:
            seen.add(key)
            deduped.append(entry)
    return deduped


def collect_audio_entries_from_payload(payload: Any) -> list[AudioEntry]:
    entries: list[AudioEntry] = []
    walk_audio_entries(payload, entries)
    deduped: list[AudioEntry] = []
    seen: set[str] = set()
    for entry in entries:
        key = entry.comment_id or entry.audio_id or entry.source_url
        if key and key not in seen:
            seen.add(key)
            deduped.append(entry)
    return deduped


def find_anchored_entries(entries: list[AudioEntry], anchor_comment_id: str) -> list[AudioEntry]:
    if not anchor_comment_id:
        return []
    return [e for e in entries if e.comment_id == anchor_comment_id or e.target_comment_id == anchor_comment_id]


def fetch_signed_h5_entries_with_playwright(
    share_url: str,
    *,
    note_id: str,
    xsec_token: str,
    xsec_source: str,
    anchor_comment_id: str,
    max_pages: int,
) -> tuple[list[AudioEntry], list[AudioEntry], int]:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as exc:
        bootstrap_capability("precision", "signed H5 comment pagination for exact anchor lookup")
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except Exception as retry_exc:
            raise RuntimeError("Playwright is required for precise signed pagination mode.") from retry_exc

    if not note_id or not xsec_token:
        raise RuntimeError("Precise pagination needs note_id and xsec_token from the share URL.")

    all_entries: list[AudioEntry] = []
    pages_read = 0
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception:
            bootstrap_capability("precision", "Playwright Chromium browser for signed H5 pagination")
            browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=UA_MOBILE,
            viewport={"width": 390, "height": 844},
            is_mobile=True,
            has_touch=True,
            locale="zh-CN",
        )
        page = context.new_page()
        page.goto(share_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_function("typeof window._webmsxyw === 'function'", timeout=30000)

        cursor = ""
        seen_cursors: set[str] = set()
        for _ in range(max_pages):
            params = {
                "note_id": note_id,
                "xsec_token": xsec_token,
                "xsec_source": xsec_source or "app_share",
                "cursor": cursor,
                "num": "20",
            }
            path = "/api/sns/h5/v1/comment/page?" + urllib.parse.urlencode(params)
            result = page.evaluate(
                """async ({path}) => {
                    const sign = window._webmsxyw(path, undefined) || {};
                    const headers = {
                        "accept": "application/json, text/plain, */*",
                        "xy-common-params": "mlanguage=zh_cn",
                        ...sign
                    };
                    const response = await fetch("https://edith.xiaohongshu.com" + path, {
                        credentials: "include",
                        headers
                    });
                    return {status: response.status, text: await response.text()};
                }""",
                {"path": path},
            )
            pages_read += 1
            if int(result["status"]) != 200:
                raise RuntimeError(f"Signed H5 comment request failed with HTTP {result['status']}: {result['text'][:400]}")
            payload = json.loads(result["text"])
            if not payload.get("success"):
                raise RuntimeError(f"Signed H5 comment request failed: {payload}")
            data = payload.get("data") or {}
            entries = collect_audio_entries_from_payload(data.get("comments") or [])
            all_entries.extend(entries)
            anchored = find_anchored_entries(entries, anchor_comment_id)
            if anchored:
                browser.close()
                return dedupe_entries(all_entries), anchored, pages_read
            next_cursor = str(data.get("cursor") or "")
            if not data.get("has_more") or not next_cursor or next_cursor in seen_cursors:
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        browser.close()
    return dedupe_entries(all_entries), find_anchored_entries(all_entries, anchor_comment_id), pages_read


def dedupe_entries(entries: list[AudioEntry]) -> list[AudioEntry]:
    deduped: list[AudioEntry] = []
    seen: set[str] = set()
    for entry in entries:
        key = entry.comment_id or entry.audio_id or entry.source_url
        if key and key not in seen:
            seen.add(key)
            deduped.append(entry)
    return deduped


def select_entries(
    entries: list[AudioEntry],
    *,
    anchor_comment_id: str,
    index: int | None,
    match: str,
    all_entries: bool,
) -> list[AudioEntry]:
    if not entries:
        return []
    if all_entries:
        return entries
    if index is not None:
        if index < 1 or index > len(entries):
            raise RuntimeError(f"--index must be between 1 and {len(entries)}.")
        return [entries[index - 1]]
    if match:
        needle = match.lower()
        matched = [
            e
            for e in entries
            if needle
            in "\n".join(
                [e.comment_id, e.target_comment_id, e.audio_id, e.nickname, e.content, e.asr_text, str(e.duration_s)]
            ).lower()
        ]
        if matched:
            return matched
        raise RuntimeError(f"No audio candidate matched {match!r}.")
    if anchor_comment_id:
        anchored = find_anchored_entries(entries, anchor_comment_id)
        if anchored:
            return anchored
    if len(entries) == 1:
        return entries
    # The shared anchor is sometimes absent from the no-login H5 payload. Download all visible
    # candidates instead of guessing incorrectly.
    return entries


def fetch_page_entries(client: XHSClient, url: str) -> tuple[dict[str, str], list[AudioEntry]]:
    html_bytes, final_url, _ = client.request(url, accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
    html = html_bytes.decode("utf-8", "replace")
    state = extract_initial_state(html)
    context = parse_note_context(final_url, state)
    entries = collect_audio_entries(state)
    return context, entries


def ffmpeg_path(*, auto_setup: bool = False) -> str:
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        if auto_setup:
            bootstrap_capability("convert", "bundled ffmpeg for audio conversion")
            try:
                import imageio_ffmpeg  # type: ignore

                return imageio_ffmpeg.get_ffmpeg_exe()
            except Exception:
                return ""
        return ""


def afconvert_path() -> str:
    return shutil.which("afconvert") or ""


def convert_with_afconvert(src: Path, dst: Path) -> bool:
    afconvert = afconvert_path()
    if not afconvert:
        return False
    cmd = [afconvert, str(src), str(dst), "-f", "WAVE", "-d", "LEI16@44100"]
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc.returncode == 0 and dst.exists() and dst.stat().st_size > 0


def convert_audio(src: Path, dst: Path, fmt: str) -> None:
    if fmt == "wav" and convert_with_afconvert(src, dst):
        return
    ffmpeg = ffmpeg_path(auto_setup=True)
    if not ffmpeg:
        raise RuntimeError("ffmpeg is unavailable and bundled converter setup failed")
    if fmt == "wav":
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vn", "-acodec", "pcm_s16le", str(dst)]
    elif fmt == "mp3":
        cmd = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-vn",
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "2",
            str(dst),
        ]
    else:
        raise ValueError(fmt)
    subprocess.run(cmd, check=True)


def target_formats(fmt: str) -> list[str]:
    if fmt == "both":
        return ["mp3", "wav"]
    if fmt in {"mp3", "wav"}:
        return [fmt]
    raise ValueError(fmt)


def unique_output_stem(out_dir: Path, stem: str, extensions: list[str], *, overwrite: bool) -> str:
    if overwrite or not extensions:
        return stem
    candidate = stem
    i = 2
    while any((out_dir / f"{candidate}.{ext}").exists() for ext in extensions):
        candidate = f"{stem}_{i}"
        i += 1
    return candidate


def download_one(
    client: XHSClient,
    entry: AudioEntry,
    *,
    out_dir: Path,
    title: str,
    fmt: str,
    name_source: str,
    overwrite: bool,
) -> dict[str, Any]:
    stem = filename_stem_for_entry(entry, title=title, name_source=name_source)
    chosen_formats = target_formats(fmt)
    stem = unique_output_stem(out_dir, stem, chosen_formats, overwrite=overwrite)

    data, _, content_type = client.request(entry.source_url, referer="https://www.xiaohongshu.com/")
    tmp = tempfile.NamedTemporaryFile(prefix=f".{stem}.", suffix=".mp4", dir=out_dir, delete=False)
    tmp.close()
    mp4_path = Path(tmp.name)
    mp4_path.write_bytes(data)

    outputs = {}
    conversion_errors: list[str] = []
    try:
        for target_fmt in chosen_formats:
            out_path = out_dir / f"{stem}.{target_fmt}"
            if overwrite:
                out_path.unlink(missing_ok=True)
            try:
                convert_audio(mp4_path, out_path, target_fmt)
                outputs[target_fmt] = str(out_path)
            except (subprocess.CalledProcessError, RuntimeError) as exc:
                conversion_errors.append(f"conversion failed for {target_fmt}: {exc}")
    finally:
        mp4_path.unlink(missing_ok=True)

    return {
        "entry": asdict(entry) | {"duration_s": entry.duration_s},
        "content_type": content_type,
        "bytes": len(data),
        "outputs": outputs,
        "conversion_errors": conversion_errors,
    }


def print_candidates(context: dict[str, str], entries: list[AudioEntry], selected: list[AudioEntry]) -> None:
    print(f"Final URL: {context.get('final_url')}")
    print(f"Note: {context.get('note_id')}  Anchor: {context.get('anchor_comment_id') or '-'}")
    print(f"Found {len(entries)} audio candidate(s); selected {len(selected)}.")
    selected_ids = {id(e) for e in selected}
    for idx, entry in enumerate(entries, 1):
        mark = "*" if id(entry) in selected_ids else " "
        text = entry.asr_text or entry.content or "(no text)"
        if len(text) > 72:
            text = text[:69] + "..."
        print(
            f"{mark} [{idx}] {entry.duration_s:g}s  {entry.nickname or '-'}  "
            f"comment={entry.comment_id or '-'}  target={entry.target_comment_id or '-'}  {text}"
        )


def direct_audio_entry(url: str) -> AudioEntry:
    return AudioEntry(
        comment_id="",
        target_comment_id="",
        note_id="",
        audio_id="",
        duration_ms=0,
        nickname="direct",
        user_id="",
        content="",
        asr_text="",
        tag_text="",
        source_url=url,
    )


def run(args: argparse.Namespace) -> int:
    urls = urls_from_input_items(args.input)
    if not urls:
        raise RuntimeError("No URL found. Pass an XHS share link, copied share text, or a QR-code image path.")

    out_dir = Path(args.output).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    client = XHSClient(cookie=args.cookie or os.environ.get("XHS_COOKIE", ""))
    all_results: list[dict[str, Any]] = []
    had_errors = False

    for url in urls:
        used_precise_pagination = False
        precise_pages_read = 0
        selected: list[AudioEntry] = []
        if "sns-video" in url and ".mp4" in urllib.parse.urlparse(url).path:
            context = {"final_url": url, "note_id": "", "anchor_comment_id": "", "title": "xhs_direct_audio"}
            entries = [direct_audio_entry(url)]
        else:
            context, entries = fetch_page_entries(client, url)
            anchor = context.get("anchor_comment_id", "")
            if anchor and args.precision != "never" and not any([args.index, args.match, args.all]):
                try:
                    precise_entries, anchored, precise_pages_read = fetch_signed_h5_entries_with_playwright(
                        url,
                        note_id=context.get("note_id", ""),
                        xsec_token=context.get("xsec_token", ""),
                        xsec_source=context.get("xsec_source", "app_share"),
                        anchor_comment_id=anchor,
                        max_pages=args.max_pages,
                    )
                    used_precise_pagination = True
                    if precise_entries:
                        entries = precise_entries
                    selected = anchored
                except Exception as exc:
                    if args.precision == "required":
                        raise
                    print(f"Warning: precise signed pagination was unavailable: {exc}", file=sys.stderr)
                    selected = []
            else:
                selected = []
        if not selected:
            selected = select_entries(
                entries,
                anchor_comment_id=context.get("anchor_comment_id", ""),
                index=args.index,
                match=args.match,
                all_entries=args.all,
            )
            if (
                context.get("anchor_comment_id")
                and not any([args.index, args.match, args.all, args.fallback_visible])
                and not find_anchored_entries(selected, context.get("anchor_comment_id", ""))
            ):
                selected = []
        print_candidates(context, entries, selected)
        if used_precise_pagination:
            print(f"Precise pagination: searched {precise_pages_read} signed H5 comment page(s).")
        if not selected:
            print("No exact downloadable voice-comment audio was selected.", file=sys.stderr)
            if context.get("anchor_comment_id"):
                print(
                    "The share has an anchorCommentId, but the target was not found. "
                    "Use --precision required after installing Playwright, pass --fallback-visible to download visible candidates, "
                    "or provide a direct sns-video URL.",
                    file=sys.stderr,
                )
            continue
        for entry in selected:
            result = download_one(
                client,
                entry,
                out_dir=out_dir,
                title=context.get("title", ""),
                fmt=args.format,
                name_source=args.name_source,
                overwrite=args.overwrite,
            )
            result["context"] = context
            all_results.append(result)
            for kind, path in result["outputs"].items():
                print(f"Saved {kind.upper()}: {path}")
            for err in result["conversion_errors"]:
                had_errors = True
                print(f"Warning: {err}", file=sys.stderr)

    if args.write_manifest:
        manifest = out_dir / "xhs_voice_comment_manifest.json"
        manifest.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Saved metadata: {manifest}")
    if not all_results:
        return 1
    return 2 if had_errors else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download Xiaohongshu voice-comment audio and convert to WAV/MP3.")
    parser.add_argument("input", nargs="+", help="XHS URL/share text, direct sns-video MP4 URL, text file, or QR image path.")
    parser.add_argument("-o", "--output", default="xhs_audio_exports", help="Output directory.")
    parser.add_argument(
        "-f",
        "--format",
        choices=["both", "mp3", "wav"],
        default="both",
        help="Target output format. Default saves both MP3 and WAV.",
    )
    parser.add_argument(
        "--name-source",
        choices=["asr", "metadata"],
        default="asr",
        help="Filename source. Default uses ASR/comment text, falling back to metadata when text is unavailable.",
    )
    parser.add_argument("--all", action="store_true", help="Download every audio candidate found in the H5 payload.")
    parser.add_argument("--index", type=int, help="Download one candidate by 1-based index after listing candidates.")
    parser.add_argument("--match", default="", help="Download candidates whose nickname/text/comment id/duration contains this string.")
    parser.add_argument(
        "--precision",
        choices=["auto", "never", "required"],
        default="auto",
        help="For anchored share links, use signed H5 pagination via Playwright to find the exact comment.",
    )
    parser.add_argument("--max-pages", type=int, default=40, help="Maximum signed H5 comment pages to scan in precise mode.")
    parser.add_argument(
        "--fallback-visible",
        action="store_true",
        help="If an anchored target is not found, download visible candidates instead of stopping.",
    )
    parser.add_argument("--cookie", default="", help="Optional XHS cookie string. Can also be set with XHS_COOKIE.")
    parser.add_argument("--write-manifest", action="store_true", help="Write xhs_voice_comment_manifest.json metadata.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing output files with the same name.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return run(args)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
