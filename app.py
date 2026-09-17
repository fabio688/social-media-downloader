import re
import io
import time
import json
from urllib.parse import urlparse

from flask import Flask, render_template, request, jsonify, Response
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
    response = requests.get(post_url, headers=HEADERS, timeout=15, allow_redirects=True)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    result = {"title": None, "images": [], "videos": []}

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
    }
    try:
        import curl_cffi  # noqa: F401
        from yt_dlp.networking.impersonate import ImpersonateTarget
        options["impersonate"] = ImpersonateTarget("chrome", "123", "macos", "14")
    except ImportError:
        pass
    with YoutubeDL(options) as downloader:
        info = downloader.extract_info(post_url, download=False)

    result["title"] = result["title"] or info.get("title")
    thumbnail = info.get("thumbnail")
    if thumbnail and thumbnail not in result["images"]:
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
            "error": "Inserisci un link pubblico di LinkedIn, Instagram o TikTok."
        }), 400

    try:
        media = extract_media(url) if platform == "LinkedIn" else extract_social_media(url)
    except requests.RequestException:
        return jsonify({"error": "La piattaforma non ha risposto. Riprova tra poco."}), 502
    except DownloadError:
        return jsonify({
            "error": (
                "Questo contenuto non è accessibile pubblicamente senza login "
                "oppure la piattaforma ha limitato la richiesta."
            )
        }), 502

    if not media["images"] and not media["videos"]:
        return jsonify({
            "error": (
                "Nessun media trovato. Il post potrebbe essere privato o richiedere il login, "
                "oppure LinkedIn potrebbe aver cambiato la struttura della pagina nel frattempo."
            )
        }), 404

    return jsonify(media)


@app.route("/api/proxy-download")
def proxy_download():
    """Scarica il file media lato server e lo inoltra al browser, evitando problemi di CORS
    e permettendo di impostare un nome file corretto."""
    media_url = request.args.get("url")
    filename = request.args.get("filename") or f"linkedin-media-{int(time.time())}"

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
