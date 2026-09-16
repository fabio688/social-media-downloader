import re
import io
import time
import json
from urllib.parse import urlparse

from flask import Flask, render_template, request, jsonify, Response
import requests
from bs4 import BeautifulSoup

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


def is_linkedin_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme in {"http", "https"} and (
        hostname == "linkedin.com"
        or hostname.endswith(".linkedin.com")
        or hostname == "lnkd.in"
    )


def is_allowed_media_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme == "https" and (
        hostname == "licdn.com"
        or hostname.endswith(".licdn.com")
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


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/api/extract", methods=["POST"])
def api_extract():
    data = request.get_json(force=True, silent=True) or {}
    url = data.get("url", "").strip()

    if not is_linkedin_url(url):
        return jsonify({"error": "Inserisci un link valido di un post LinkedIn."}), 400

    try:
        media = extract_media(url)
    except requests.RequestException as exc:
        return jsonify({"error": f"Errore nel recupero della pagina: {exc}"}), 502

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
