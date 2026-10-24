import re
import io
import time
import json
import os
import shutil
import tempfile
from urllib.parse import urlparse

from flask import Flask, render_template, request, jsonify, Response, send_file, after_this_request
import requests
from bs4 import BeautifulSoup
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

app = Flask(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}

VIDEO_URL_PATTERN = re.compile(r'https:\\?/\\?/[^"\'\\]+?\.(?:mp4|m3u8)[^"\'\\]*')


def clean_url(raw_url: str) -> str:
    """Rimuove escape residui (es. \\/ ) che a volte compaiono nel JSON incorporato."""
    return raw_url.replace("\\/", "/").replace("\\u0026", "&")


def is_quicktime_format(video_format: dict) -> bool:
    video_codec = video_format.get("vcodec") or ""
    audio_codec = video_format.get("acodec") or ""
    return video_codec.startswith("avc1") and audio_codec.startswith("mp4a")


def is_linkedin_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme in {"http", "https"} and (
        hostname == "linkedin.com"
        or hostname.endswith(".linkedin.com")
        or hostname == "lnkd.in"
    )


def get_platform(url: str) -> str | None:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"}:
        return None
    if hostname == "linkedin.com" or hostname.endswith(".linkedin.com") or hostname == "lnkd.in":
        return "LinkedIn"
    if hostname == "instagram.com" or hostname.endswith(".instagram.com"):
        return "Instagram"
    if (
        hostname == "tiktok.com"
        or hostname.endswith(".tiktok.com")
        or hostname in {"vm.tiktok.com", "vt.tiktok.com"}
    ):
        return "TikTok"
    if (
        hostname == "youtube.com"
        or hostname.endswith(".youtube.com")
        or hostname == "youtube-nocookie.com"
        or hostname.endswith(".youtube-nocookie.com")
        or hostname == "youtu.be"
    ):
        return "YouTube"
    return None


def is_allowed_media_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme == "https" and (
        hostname == "licdn.com"
        or hostname.endswith(".licdn.com")
        or hostname == "instagram.com"
        or hostname.endswith(".instagram.com")
        or hostname.endswith(".cdninstagram.com")
        or hostname == "fbcdn.net"
        or hostname.endswith(".fbcdn.net")
        or hostname == "tiktokcdn.com"
        or hostname.endswith(".tiktokcdn.com")
        or hostname == "tiktok.com"
        or hostname.endswith(".tiktok.com")
        or hostname == "tiktokcdn-us.com"
        or hostname.endswith(".tiktokcdn-us.com")
        or hostname == "tiktokcdn-eu.com"
        or hostname.endswith(".tiktokcdn-eu.com")
        or hostname == "tiktokv.com"
        or hostname.endswith(".tiktokv.com")
        or hostname == "ibytedtos.com"
        or hostname.endswith(".ibytedtos.com")
        or hostname == "byteoversea.com"
        or hostname.endswith(".byteoversea.com")
        or hostname == "muscdn.com"
        or hostname.endswith(".muscdn.com")
    )


def extract_media(post_url: str) -> dict:
    # requests segue i redirect di lnkd.in e resp.text e' il contenuto dell'URL finale.
    resp = requests.get(post_url, headers=HEADERS, timeout=15, allow_redirects=True)
    resp.raise_for_status()
    html = resp.text
    soup = BeautifulSoup(html, "html.parser")

    result = {"title": None, "images": [], "videos": []}

    title_tag = soup.find("meta", property="og:title")
    if title_tag and title_tag.get("content"):
        result["title"] = title_tag["content"]

    # Immagini: i meta tag Open Graph sono quasi sempre presenti anche senza login,
    # perché servono per le anteprime social (WhatsApp, Facebook, ecc.)
    for tag in soup.find_all("meta", property="og:image"):
        content = tag.get("content")
        if content and content not in result["images"]:
            result["images"].append(clean_url(content))

    # Video: prima i meta tag standard...
    for prop in ("og:video", "og:video:secure_url", "og:video:url"):
        for tag in soup.find_all("meta", property=prop):
            content = tag.get("content")
            if content and content not in result["videos"]:
                result["videos"].append(clean_url(content))

    # LinkedIn inserisce spesso le sorgenti in data-sources o in VideoObject JSON-LD.
    video_sources = []
    for tag in soup.select("[data-sources]"):
        raw_sources = tag.get("data-sources")
        if not raw_sources:
            continue
        try:
            sources = json.loads(raw_sources)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(sources, list):
            video_sources.extend(
                source for source in sources
                if isinstance(source, dict) and source.get("src")
            )

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            structured_data = json.loads(tag.string or tag.get_text())
        except (TypeError, json.JSONDecodeError):
            continue
        structured_items = structured_data if isinstance(structured_data, list) else [structured_data]
        for item in structured_items:
            if isinstance(item, dict) and item.get("contentUrl"):
                video_sources.append({"src": item["contentUrl"], "data-bitrate": 0})

    for source in sorted(video_sources, key=lambda item: item.get("data-bitrate", 0), reverse=True):
        video_url = clean_url(source["src"])
        if video_url not in result["videos"]:
            result["videos"].append(video_url)

    # Fallback: cerca URL video nel sorgente per eventuali varianti non strutturate.
    if not result["videos"]:
        for match in VIDEO_URL_PATTERN.findall(html):
            cleaned = clean_url(match)
            if cleaned not in result["videos"]:
                result["videos"].append(cleaned)

    return result


def extract_social_media(post_url: str) -> dict:
    result = {"title": None, "images": [], "videos": []}

    if get_platform(post_url) != "YouTube":
        response = requests.get(post_url, headers=HEADERS, timeout=15, allow_redirects=True)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        title_tag = soup.find("meta", property="og:title")
        if title_tag and title_tag.get("content"):
            result["title"] = title_tag["content"]
        for tag in soup.find_all("meta", property="og:image"):
            content = tag.get("content")
            if content:
                result["images"].append(clean_url(content))
        for prop in ("og:video", "og:video:secure_url", "og:video:url"):
            for tag in soup.find_all("meta", property=prop):
                content = tag.get("content")
                if content:
                    result["videos"].append(clean_url(content))

    if result["videos"]:
        return result

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "http_headers": HEADERS,
        "socket_timeout": 15,
        "retries": 1,
        "extractor_retries": 1,
        "format": (
            "best[ext=mp4][vcodec^=avc1][acodec^=mp4a]/"
            "best[vcodec^=avc1][acodec^=mp4a]/best[ext=mp4]/bestvideo*/best"
        ),
        # Per YouTube serve l'errore vero (es. blocco "not a bot") per log e messaggio.
        "ignore_no_formats_error": get_platform(post_url) != "YouTube",
    }
    try:
        import curl_cffi  # noqa: F401
        from yt_dlp.networking.impersonate import ImpersonateTarget
        options["impersonate"] = ImpersonateTarget("chrome", "123", "macos", "14")
    except ImportError:
        pass
    probe_dir = tempfile.mkdtemp(prefix="social-probe-")
    apply_youtube_options(options, post_url, probe_dir)
    try:
        with YoutubeDL(options) as downloader:
            info = downloader.extract_info(post_url, download=False)
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)

    result["title"] = result["title"] or info.get("title")
    thumbnail = info.get("thumbnail")
    if thumbnail and thumbnail not in result["images"] and get_platform(post_url) != "YouTube":
        result["images"].append(thumbnail)

    formats = info.get("formats") or []
    video_formats = [
        item for item in formats
        if item.get("url") and item.get("vcodec") not in {None, "none"}
    ]
    video_formats.sort(
        key=lambda item: (
            is_quicktime_format(item),
            item.get("height") or 0,
            item.get("tbr") or 0,
        ),
        reverse=True,
    )
    if video_formats:
        result["videos"].append(video_formats[0]["url"])
    elif info.get("url") and info.get("vcodec") not in {None, "none"}:
        result["videos"].append(info["url"])
    return result


def apply_youtube_options(options: dict, url: str, temp_dir: str):
    """Proxy (YOUTUBE_PROXY), cookie (YOUTUBE_COOKIES) e client di riserva per aggirare il blocco IP."""
    if get_platform(url) != "YouTube":
        return
    proxy = os.environ.get("YOUTUBE_PROXY", "").strip()
    if proxy:
        options["proxy"] = proxy
    cookies_path = write_youtube_cookies(temp_dir)
    if cookies_path:
        options["cookiefile"] = cookies_path
    options["extractor_args"] = {"youtube": {"player_client": ["tv", "web_safari", "mweb", "default"]}}


YOUTUBE_MAX_FILESIZE = 500 * 1024 * 1024
YOUTUBE_MAX_DURATION = 60 * 60


def youtube_match_filter(info, *, incomplete=False):
    """Esclude dirette e video troppo lunghi (disco/RAM limitati su Render)."""
    if info.get("is_live"):
        raise DownloadError("video is live")
    if (info.get("duration") or 0) > YOUTUBE_MAX_DURATION:
        raise DownloadError("video is too long")
    return None


YOUTUBE_BLOCK_MESSAGE = (
    "YouTube ha bloccato la richiesta dal server (succede spesso con i server cloud) "
    "o il video non è pubblico. Riprova più tardi: non tutti i video YouTube sono scaricabili."
)


def youtube_error_message(error: Exception) -> str:
    """Traduce i fallimenti di yt-dlp su YouTube in un messaggio chiaro, senza promettere nulla."""
    text = str(error).lower()
    if "confirm your age" in text or "age-restricted" in text or "inappropriate for some" in text:
        return "Questo video richiede l'accesso per verificare l'età: non è scaricabile."
    if "private video" in text or "members-only" in text or "members only" in text:
        return "Il video è privato o riservato ai membri: si possono scaricare solo video pubblici."
    if "is live" in text or "live event" in text or "livestream" in text:
        return "Le dirette YouTube non sono scaricabili."
    if "larger than max-filesize" in text or "max-filesize" in text or "too long" in text:
        return "Il video è troppo grande o troppo lungo per essere scaricato da questo servizio."
    return YOUTUBE_BLOCK_MESSAGE


def find_ffmpeg():
    """ffmpeg di sistema, oppure il binario statico incluso in imageio-ffmpeg."""
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def write_youtube_cookies(temp_dir: str):
    """Cookie opzionali (formato Netscape) dalla variabile d'ambiente YOUTUBE_COOKIES:
    servono quando YouTube blocca gli IP dei datacenter con 'Sign in to confirm you're not a bot'."""
    cookies = os.environ.get("YOUTUBE_COOKIES", "").strip()
    secret_file = "/etc/secrets/youtube_cookies.txt"  # Secret File di Render
    if not cookies and os.path.isfile(secret_file):
        with open(secret_file) as handle:
            cookies = handle.read().strip()
    if not cookies:
        return None
    path = os.path.join(temp_dir, "cookies.txt")
    with open(path, "w") as handle:
        handle.write(cookies + "\n")
    return path


def download_social_media(post_url: str, filename: str):
    temp_dir = tempfile.mkdtemp(prefix="social-download-")
    output_template = os.path.join(temp_dir, "media.%(ext)s")
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "noprogress": True,
        "outtmpl": output_template,
        "format": (
            "bestvideo[ext=mp4][vcodec^=avc1][height<=1080]+bestaudio[ext=m4a]/"
            "bestvideo[height<=1080]+bestaudio/best[ext=mp4]/best"
        ),
        "merge_output_format": "mp4",
        "http_headers": HEADERS,
        "socket_timeout": 30,
        "retries": 2,
        "extractor_retries": 2,
    }
    ffmpeg_path = find_ffmpeg()
    if ffmpeg_path:
        options["ffmpeg_location"] = ffmpeg_path
    apply_youtube_options(options, post_url, temp_dir)
    if get_platform(post_url) == "YouTube":
        options["max_filesize"] = YOUTUBE_MAX_FILESIZE
        options["match_filter"] = youtube_match_filter

    try:
        import curl_cffi  # noqa: F401
        from yt_dlp.networking.impersonate import ImpersonateTarget
        options["impersonate"] = ImpersonateTarget("chrome", "123", "macos", "14")
    except ImportError:
        pass

    try:
        with YoutubeDL(options) as downloader:
            downloader.download([post_url])
        files = [
            os.path.join(temp_dir, entry)
            for entry in os.listdir(temp_dir)
            if os.path.isfile(os.path.join(temp_dir, entry))
            and entry.startswith("media.") and not entry.endswith((".part", ".ytdl"))
        ]
        if not files:
            raise DownloadError("Il downloader non ha prodotto alcun file.")
        media_path = files[0]
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    @after_this_request
    def remove_temporary_file(response):
        shutil.rmtree(temp_dir, ignore_errors=True)
        return response

    response = send_file(media_path, as_attachment=True, download_name=filename, mimetype="video/mp4")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/api/extract", methods=["POST"])
def api_extract():
    data = request.get_json(force=True, silent=True) or {}
    url = data.get("url", "").strip()

    platform = get_platform(url)
    if not platform:
        return jsonify({
            "error": "Inserisci un link pubblico di LinkedIn, Instagram, TikTok o YouTube."
        }), 400

    try:
        media = extract_media(url) if platform == "LinkedIn" else extract_social_media(url)
        if platform != "LinkedIn":
            media["download_source"] = url
    except requests.RequestException:
        return jsonify({"error": "La piattaforma non ha risposto. Riprova tra poco."}), 502
    except DownloadError as error:
        app.logger.error("Analisi fallita per %s: %s", url, error)
        if platform == "YouTube":
            return jsonify({"error": youtube_error_message(error)}), 502
        return jsonify({
            "error": (
                "Questo contenuto non è accessibile pubblicamente senza login "
                "oppure la piattaforma ha limitato la richiesta."
            )
        }), 502
    except Exception:
        app.logger.exception("Errore inatteso nell'analisi di %s", url)
        if platform == "YouTube":
            return jsonify({"error": YOUTUBE_BLOCK_MESSAGE}), 502
        return jsonify({"error": "Errore inatteso durante l'analisi. Riprova più tardi."}), 500

    if platform == "YouTube" and not media["videos"]:
        app.logger.error("Analisi YouTube senza formati video per %s", url)
        return jsonify({"error": YOUTUBE_BLOCK_MESSAGE}), 502

    if not media["images"] and not media["videos"]:
        return jsonify({
            "error": (
                "Nessun media trovato. Il contenuto potrebbe essere privato, richiedere il login "
                "oppure la piattaforma potrebbe aver cambiato la propria struttura."
            )
        }), 404

    return jsonify(media)


@app.route("/api/social-download")
def social_download():
    source_url = request.args.get("url", "").strip()
    platform = get_platform(source_url)
    if platform not in {"Instagram", "TikTok", "YouTube"}:
        return "URL Instagram, TikTok o YouTube non valida.", 400
    if platform == "YouTube" and request.args.get("rights_confirmed") != "1":
        return "Conferma di avere il diritto di scaricare questo video.", 400

    try:
        return download_social_media(source_url, f"social-{platform.lower()}-{int(time.time())}.mp4")
    except DownloadError as error:
        app.logger.error("Download fallito per %s: %s", source_url, error)
        if platform == "YouTube":
            return youtube_error_message(error), 502
        return "Il video non è disponibile pubblicamente o la piattaforma ha bloccato la richiesta.", 502
    except Exception:
        app.logger.exception("Errore inatteso nel download di %s", source_url)
        return "Errore inatteso durante il download. Riprova più tardi.", 500


@app.route("/api/proxy-download")
def proxy_download():
    """Scarica il file media lato server e lo inoltra al browser, evitando problemi di CORS
    e permettendo di impostare un nome file corretto."""
    media_url = request.args.get("url")
    filename = re.sub(r'[^A-Za-z0-9._-]', "_", request.args.get("filename") or "") or f"linkedin-media-{int(time.time())}"

    if not media_url:
        return "Parametro 'url' mancante.", 400
    if not is_allowed_media_url(media_url):
        return "URL media non consentito.", 400

    try:
        upstream = requests.get(
            media_url,
            headers=HEADERS,
            stream=True,
            timeout=30,
            allow_redirects=False,
        )
        upstream.raise_for_status()
    except requests.RequestException as exc:
        return f"Errore durante il download del file: {exc}", 502

    content_type = upstream.headers.get("Content-Type", "application/octet-stream")

    def generate():
        for chunk in upstream.iter_content(chunk_size=8192):
            if chunk:
                yield chunk

    return Response(
        generate(),
        content_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
