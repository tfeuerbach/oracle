import asyncio
import logging
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import aiohttp
import openai
import yt_dlp

from .config import (
    MAX_CONCURRENT_TRANSCRIPTIONS,
    MAX_VIDEO_DURATION,
    OPENAI_API_KEY,
    TEMP_DIR,
    VIDEO_URL_DOMAINS,
    WHISPER_MAX_FILE_BYTES,
    WHISPER_MODE,
    WHISPER_MODEL,
    LOCAL_WHISPER_MODEL,
    YTDLP_COOKIES_FILE,
    YTDLP_COOKIES_BROWSER,
)

log = logging.getLogger("oracle.transcriber")

live_semaphore = asyncio.Semaphore(MAX_CONCURRENT_TRANSCRIPTIONS)
backfill_semaphore = asyncio.Semaphore(max(1, MAX_CONCURRENT_TRANSCRIPTIONS - 2))

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")

YOUTUBE_NON_VIDEO_RE = re.compile(
    r"youtube\.com/(@|channel/|c/|user/|feeds?|playlist\?|results\?|about)",
    re.IGNORECASE,
)

REDDIT_NON_VIDEO_RE = re.compile(
    r"reddit\.com/(r/[^/]+/?$|r/[^/]+/?(wiki|about|rules|search))",
    re.IGNORECASE,
)

TWITTER_NON_VIDEO_RE = re.compile(
    r"(twitter\.com|x\.com)/(?!.*/status/)",
    re.IGNORECASE,
)


def extract_video_urls(text: str):
    """Return [(url, source_type), ...] for known video platform URLs in text."""
    results = []
    for match in URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;:!?")
        domain = urlparse(url).netloc.lower()
        source = VIDEO_URL_DOMAINS.get(domain)
        if not source:
            continue
        if source == "youtube" and YOUTUBE_NON_VIDEO_RE.search(url):
            continue
        if source == "reddit" and REDDIT_NON_VIDEO_RE.search(url):
            continue
        if source == "twitter" and TWITTER_NON_VIDEO_RE.search(url):
            continue
        results.append((url, source))
    return results


async def resolve_redirect(url: str):
    """Follow HTTP redirects to get the final URL (e.g. Reddit /s/ share links)."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=10),
                headers={"User-Agent": "Oracle/1.0"},
            ) as resp:
                resolved = str(resp.url)
    except Exception:
        return url

    parsed = urlparse(resolved)
    if "reddit.com" in parsed.netloc:
        cleaned = urlunparse(parsed._replace(query="", fragment=""))
        return cleaned.rstrip("/")
    return resolved


REDDIT_HEADERS = {"User-Agent": "Oracle/1.0"}


async def fetch_reddit_post(url: str, timeout: int = 15):
    """Fetch and parse a Reddit post's JSON data. Returns (post_data, title) or None."""
    parsed = urlparse(url)
    if "reddit.com" not in parsed.netloc:
        return None

    json_url = url.rstrip("/") + ".json"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(json_url, headers=REDDIT_HEADERS, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
        post_data = data[0]["data"]["children"][0]["data"]
        title = post_data.get("title", url)
        return post_data, title
    except Exception:
        log.warning("Reddit JSON API request failed for %s", url)
        return None


def reddit_post_has_video(post_data):
    """Check if a Reddit post (or its crossposts) contains video."""
    if post_data.get("is_video", False):
        return True
    return any(xp.get("is_video", False) for xp in post_data.get("crosspost_parent_list", []))


def reddit_find_video_url(post_data):
    """Extract the fallback video URL from Reddit post data."""
    for key in ("media", "secure_media"):
        url = (post_data.get(key) or {}).get("reddit_video", {}).get("fallback_url")
        if url:
            return url
    for xp in post_data.get("crosspost_parent_list", []):
        for key in ("media", "secure_media"):
            url = (xp.get(key) or {}).get("reddit_video", {}).get("fallback_url")
            if url:
                return url
    return None


async def reddit_pre_check(url: str):
    """Quick check via Reddit JSON API to skip non-video posts before yt-dlp."""
    if "/comments/" not in urlparse(url).path:
        return None

    result = await fetch_reddit_post(url, timeout=10)
    if result is None:
        return None

    post_data, title = result
    if not reddit_post_has_video(post_data):
        hint = post_data.get("post_hint", "")
        log.info("Reddit pre-check: skipping non-video post (hint=%s): %s", hint, title)
        return None, None, "not_video", title

    return None


async def reddit_json_fallback(url: str, output_path: Path):
    """Fetch video URL from Reddit's JSON API when yt-dlp fails."""
    result = await fetch_reddit_post(url)
    if result is None:
        return None

    post_data, title = result
    post_hint = post_data.get("post_hint", "")

    if not reddit_post_has_video(post_data) and post_hint in ("image", "link"):
        log.info("Reddit post is not a video (hint=%s), skipping: %s", post_hint, title)
        return {"title": title, "duration": None, "audio_path": None, "not_video": True}

    fallback_url = reddit_find_video_url(post_data)
    if not fallback_url:
        log.warning("No reddit_video found in JSON for %s", url)
        return None

    audio_url = re.sub(r"DASH_\d+", "DASH_AUDIO_128", fallback_url.split("?")[0])
    log.info("Reddit JSON fallback: downloading audio from %s", audio_url)

    audio_dest = output_path.with_suffix(".m4a")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(audio_url, headers=REDDIT_HEADERS, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    log.info("Reddit audio stream returned %d, trying video-only", resp.status)
                    async with session.get(fallback_url, headers=REDDIT_HEADERS, timeout=aiohttp.ClientTimeout(total=60)) as vresp:
                        if vresp.status != 200:
                            return None
                        video_dest = output_path.with_suffix(".mp4")
                        with open(video_dest, "wb") as f:
                            async for chunk in vresp.content.iter_chunked(8192):
                                f.write(chunk)
                    try:
                        audio_dest = await asyncio.to_thread(extract_audio, video_dest)
                    except RuntimeError:
                        log.info("Reddit video has no audio stream: %s", title)
                        video_dest.unlink(missing_ok=True)
                        return {"title": title, "duration": None, "audio_path": None, "not_video": False, "no_audio": True}
                    video_dest.unlink(missing_ok=True)
                else:
                    with open(audio_dest, "wb") as f:
                        async for chunk in resp.content.iter_chunked(8192):
                            f.write(chunk)
    except (KeyError, IndexError, TypeError):
        log.warning("Failed to parse Reddit JSON for %s", url, exc_info=True)
        return None

    duration_val = (post_data.get("media") or {}).get("reddit_video", {}).get("duration")
    return {"title": title, "duration": duration_val, "audio_path": audio_dest}


async def download_file(url: str, dest: Path):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                async for chunk in resp.content.iter_chunked(8192):
                    f.write(chunk)
    return dest


def extract_audio(video_path: Path):
    audio_path = video_path.with_suffix(".wav")
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(video_path),
         "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
         str(audio_path)],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr}")
    return audio_path


def get_duration(file_path: Path):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1",
             str(file_path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(result.stdout.strip()) if result.returncode == 0 else None
    except (ValueError, subprocess.TimeoutExpired):
        return None


class APICredentialError(Exception):
    """Raised when the OpenAI API key is invalid or the account has no credits."""


async def transcribe_api(audio_path: Path):
    client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)
    try:
        with open(audio_path, "rb") as f:
            response = await client.audio.transcriptions.create(
                model=WHISPER_MODEL, file=f, response_format="text",
            )
        return response.strip() if isinstance(response, str) else response.text.strip()
    except openai.AuthenticationError:
        raise APICredentialError(
            "OpenAI API key is invalid or revoked. "
            "Check OPENAI_API_KEY in your .env file."
        )
    except openai.BadRequestError as e:
        if "could not be decoded" in str(e).lower() or "audio" in str(e).lower():
            log.info("Whisper rejected audio file %s (likely empty or corrupt)", audio_path.name)
            return None
        raise
    except openai.RateLimitError as e:
        msg = str(e).lower()
        if "insufficient_quota" in msg or "billing" in msg or "exceeded" in msg:
            raise APICredentialError(
                "OpenAI account has insufficient credits. "
                "Add billing at https://platform.openai.com/account/billing"
            )
        raise


async def transcribe_local(audio_path: Path):
    proc = await asyncio.create_subprocess_exec(
        "whisper", str(audio_path),
        "--model", LOCAL_WHISPER_MODEL,
        "--output_format", "txt",
        "--output_dir", str(audio_path.parent),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Local whisper failed: {stderr.decode()}")
    txt_path = audio_path.with_suffix(".txt")
    if txt_path.exists():
        text = txt_path.read_text().strip()
        txt_path.unlink(missing_ok=True)
        return text
    raise FileNotFoundError("Whisper produced no output file")


def chunk_audio(audio_path: Path):
    """Split an audio file into chunks that fit the Whisper API size limit."""
    if audio_path.stat().st_size <= WHISPER_MAX_FILE_BYTES:
        return [audio_path]

    total_size = audio_path.stat().st_size
    total_dur = get_duration(audio_path) or 600
    chunk_secs = max(60, int(total_dur * (WHISPER_MAX_FILE_BYTES / total_size) * 0.85))

    pattern = str(audio_path.parent / f"{audio_path.stem}_chunk_%03d{audio_path.suffix}")
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(audio_path),
         "-f", "segment", "-segment_time", str(chunk_secs),
         "-c", "copy", pattern],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg chunk failed: {result.stderr}")

    chunks = sorted(audio_path.parent.glob(f"{audio_path.stem}_chunk_*{audio_path.suffix}"))
    if not chunks:
        raise FileNotFoundError("ffmpeg produced no chunk files")
    return chunks


async def transcribe_file(audio_path: Path):
    """Transcribe an audio file, chunking automatically if it exceeds the API limit."""
    chunks = await asyncio.to_thread(chunk_audio, audio_path)
    parts = []
    try:
        for chunk in chunks:
            log.info("  Transcribing chunk %s (%.1f MB)", chunk.name, chunk.stat().st_size / 1e6)
            text = await (transcribe_local(chunk) if WHISPER_MODE == "local" else transcribe_api(chunk))
            if text:
                parts.append(text)
    finally:
        for chunk in chunks:
            if chunk != audio_path:
                chunk.unlink(missing_ok=True)
    return " ".join(parts)


async def process_video(
    url: str,
    filename: str,
    priority: str = "live",
):
    """Download a Discord attachment, extract audio, transcribe.

    Returns (transcription, duration, status).
    Status is 'ok', 'no_audio', 'no_speech', or 'error'.
    """
    if priority == "backfill":
        await backfill_semaphore.acquire()
    try:
        async with live_semaphore:
            video_path = TEMP_DIR / f"{id(asyncio.current_task())}_{filename}"
            audio_path = None
            try:
                log.info("Downloading %s", filename)
                await download_file(url, video_path)
                duration = get_duration(video_path)

                log.info("Extracting audio from %s", filename)
                try:
                    audio_path = await asyncio.to_thread(extract_audio, video_path)
                except RuntimeError as e:
                    if "does not contain any stream" in str(e):
                        log.info("No audio stream in %s", filename)
                        return None, duration, "no_audio"
                    raise

                video_path.unlink(missing_ok=True)
                video_path = None

                log.info("Transcribing %s (mode=%s)", filename, WHISPER_MODE)
                text = await transcribe_file(audio_path)

                if not text:
                    log.info("No speech detected in %s", filename)
                    return None, duration, "no_speech"

                log.info("Transcription complete for %s (%d chars)", filename, len(text))
                return text, duration, "ok"

            except APICredentialError:
                raise
            except Exception:
                log.exception("Failed to process %s", filename)
                return None, None, "error"
            finally:
                if video_path:
                    video_path.unlink(missing_ok=True)
                if audio_path:
                    audio_path.unlink(missing_ok=True)
    finally:
        if priority == "backfill":
            backfill_semaphore.release()


PERMANENT_FAILURE_PATTERNS = (
    "does not exist",
    "video unavailable",
    "private video",
    "has been removed",
    "this video is no longer available",
    "account associated with this video has been terminated",
    "video is not available",
    "no video could be found",
    "is not a valid url",
    "unsupported url",
    "no supported streams",
    "blocked",
    "copyright",
    "community guidelines",
    "no media found",
    "unable to obtain file audio codec",
)


def is_permanent_failure(err_msg: str):
    return any(p in err_msg for p in PERMANENT_FAILURE_PATTERNS)


class TooLong(Exception):
    """Raised when a video exceeds the max duration."""
    def __init__(self, title: str, duration: float):
        self.title = title
        self.duration = duration


def ytdlp_download(url: str, output_path: Path):
    """Download audio via yt-dlp. Returns the info dict."""
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(output_path.with_suffix(".%(ext)s")),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "socket_timeout": 15,
        "retries": 2,
        "js_runtimes": {"node": {}},
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "m4a",
            "preferredquality": "64",
        }],
        "logger": log,
    }
    if YTDLP_COOKIES_FILE:
        ydl_opts["cookiefile"] = YTDLP_COOKIES_FILE
    if YTDLP_COOKIES_BROWSER:
        ydl_opts["cookiesfrombrowser"] = (YTDLP_COOKIES_BROWSER,)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if info is None:
            raise yt_dlp.utils.DownloadError(f"No info extracted for {url}")
        duration = info.get("duration") or 0
        title = info.get("title") or url
        if duration > MAX_VIDEO_DURATION:
            raise TooLong(title, duration)
        return ydl.extract_info(url, download=True) or info


async def process_url(
    url: str,
    source: str,
    priority: str = "live",
):
    """Download audio from a URL via yt-dlp, transcribe.

    Returns (transcription, duration, status, title).
    """
    if priority == "backfill":
        await backfill_semaphore.acquire()
    try:
        async with live_semaphore:
            task_id = id(asyncio.current_task())
            audio_path = None
            try:
                if source == "reddit":
                    resolved = await resolve_redirect(url)
                    if resolved != url:
                        log.info("Resolved %s -> %s", url, resolved)
                    pre = await reddit_pre_check(resolved)
                    if pre is not None:
                        return pre
                else:
                    resolved = url
                log.info("Fetching %s (%s)", resolved, source)
                info = await asyncio.wait_for(
                    asyncio.to_thread(ytdlp_download, resolved, TEMP_DIR / f"url_{task_id}"),
                    timeout=300,
                )
                title = info.get("title") or url
                duration = info.get("duration")

                expected = TEMP_DIR / f"url_{task_id}.m4a"
                if not expected.exists():
                    candidates = list(TEMP_DIR.glob(f"url_{task_id}.*"))
                    if not candidates:
                        log.warning("yt-dlp produced no file for %s", url)
                        return None, None, "error", url
                    audio_path = candidates[0]
                else:
                    audio_path = expected

                log.info("Transcribing URL video: %s (%.1f MB)", title, audio_path.stat().st_size / 1e6)
                text = await transcribe_file(audio_path)

                if not text:
                    log.info("No speech detected in %s", title)
                    return None, duration, "no_speech", title

                log.info("Transcription complete for %s (%d chars)", title, len(text))
                return text, duration, "ok", title

            except asyncio.TimeoutError:
                log.warning("yt-dlp timed out after 5 min for %s", url)
                return None, None, "error", url
            except TooLong as e:
                m, s = divmod(int(e.duration), 60)
                h, m = divmod(m, 60)
                log.info("Skipping %s -- too long (%d:%02d:%02d > %ds limit)",
                         e.title, h, m, s, MAX_VIDEO_DURATION)
                return None, e.duration, "too_long", e.title
            except yt_dlp.utils.DownloadError as e:
                err_msg = str(e).lower()
                log.warning("yt-dlp failed for %s: %s", url, e)

                if source == "reddit":
                    fallback = await reddit_json_fallback(resolved, TEMP_DIR / f"url_{task_id}")
                    if fallback:
                        if fallback.get("not_video"):
                            return None, None, "not_video", fallback["title"]
                        if fallback.get("no_audio"):
                            return None, None, "no_audio", fallback["title"]
                        audio_path = fallback["audio_path"]
                        title = fallback["title"]
                        duration = fallback.get("duration")
                        if not audio_path or not audio_path.exists() or audio_path.stat().st_size == 0:
                            log.info("Reddit fallback produced no usable audio for %s", title)
                            return None, duration, "no_audio", title
                        log.info("Transcribing URL video (reddit fallback): %s (%.1f MB)",
                                 title, audio_path.stat().st_size / 1e6)
                        text = await transcribe_file(audio_path)
                        if not text:
                            log.info("No speech detected in %s", title)
                            return None, duration, "no_speech", title
                        log.info("Transcription complete for %s (%d chars)", title, len(text))
                        return text, duration, "ok", title

                if is_permanent_failure(err_msg):
                    log.info("Permanent failure for %s -- marking unavailable", url)
                    return None, None, "unavailable", url

                return None, None, "error", url
            except APICredentialError:
                raise
            except Exception:
                log.exception("Failed to process URL %s", url)
                return None, None, "error", url
            finally:
                if audio_path:
                    audio_path.unlink(missing_ok=True)
                for leftover in TEMP_DIR.glob(f"url_{task_id}*"):
                    leftover.unlink(missing_ok=True)
    finally:
        if priority == "backfill":
            backfill_semaphore.release()
