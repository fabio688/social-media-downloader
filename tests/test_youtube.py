import os
import sys

import pytest
from yt_dlp.utils import DownloadError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as app_module  # noqa: E402

YT = "https://www.youtube.com/watch?v=abc123"


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


class FakeYDL:
    info = {"title": "T", "thumbnail": "https://i.ytimg.com/x.jpg",
            "formats": [{"url": "https://rr.googlevideo.com/v", "vcodec": "avc1", "height": 720}]}
    error = None

    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        if self.error:
            raise self.error
        return self.info

    def download(self, urls):
        if self.error:
            raise self.error
        with open(self.options["outtmpl"] % {"ext": "mp4"}, "wb") as handle:
            handle.write(b"video")


@pytest.fixture
def fake_ydl(monkeypatch):
    FakeYDL.error = None
    monkeypatch.setattr(app_module, "YoutubeDL", FakeYDL)
    return FakeYDL


def test_youtube_platform_detection():
    assert app_module.get_platform("https://youtu.be/abc") == "YouTube"
    assert app_module.get_platform("https://m.youtube.com/watch?v=1") == "YouTube"
    assert app_module.get_platform("https://evilyoutube.com/x") is None


def test_download_requires_rights_confirmation(client, fake_ydl):
    assert client.get("/api/social-download", query_string={"url": YT}).status_code == 400
    response = client.get("/api/social-download", query_string={"url": YT, "rights_confirmed": "0"})
    assert response.status_code == 400


def test_download_with_rights_returns_file_and_cleans_up(client, fake_ydl):
    response = client.get("/api/social-download", query_string={"url": YT, "rights_confirmed": "1"})
    assert response.status_code == 200
    assert response.data == b"video"
    assert "attachment" in response.headers["Content-Disposition"]


def test_download_block_returns_clear_error(client, fake_ydl):
    fake_ydl.error = DownloadError("Sign in to confirm you're not a bot")
    response = client.get("/api/social-download", query_string={"url": YT, "rights_confirmed": "1"})
    assert response.status_code == 502
    assert response.get_data(as_text=True) == app_module.YOUTUBE_BLOCK_MESSAGE


def test_extract_youtube_ok_has_video_and_no_thumbnail(client, fake_ydl):
    response = client.post("/api/extract", json={"url": YT})
    assert response.status_code == 200
    data = response.get_json()
    assert data["videos"] and data["images"] == []
    assert data["download_source"] == YT


def test_extract_youtube_block_returns_clear_error(client, fake_ydl):
    fake_ydl.error = DownloadError("HTTP Error 429")
    response = client.post("/api/extract", json={"url": YT})
    assert response.status_code == 502
    assert response.get_json()["error"] == app_module.YOUTUBE_BLOCK_MESSAGE


def test_frontend_has_no_turboscribe_redirect(client):
    html = client.get("/").get_data(as_text=True)
    assert "turboscribe" not in html.lower()
    assert "rightsConfirm" in html


@pytest.mark.parametrize("message,expected", [
    ("Sign in to confirm your age", "età"),
    ("Private video. Sign in if you've been granted access", "privato"),
    ("video is live", "dirette"),
    ("video is too long", "troppo"),
    ("Sign in to confirm you're not a bot", app_module.YOUTUBE_BLOCK_MESSAGE),
])
def test_error_messages_are_distinct(message, expected):
    assert expected in app_module.youtube_error_message(DownloadError(message))


def test_match_filter_rejects_live_and_long():
    with pytest.raises(DownloadError):
        app_module.youtube_match_filter({"is_live": True})
    with pytest.raises(DownloadError):
        app_module.youtube_match_filter({"duration": 99999})
    assert app_module.youtube_match_filter({"duration": 60}) is None


def test_temp_dir_removed_with_cookies(client, fake_ydl, monkeypatch, tmp_path):
    monkeypatch.setenv("YOUTUBE_COOKIES", "# Netscape\n.youtube.com\tTRUE\t/\tTRUE\t0\tA\tB")
    dirs = []
    real = app_module.tempfile.mkdtemp
    monkeypatch.setattr(app_module.tempfile, "mkdtemp", lambda **kw: dirs.append(real(dir=tmp_path, **kw)) or dirs[-1])
    assert client.get("/api/social-download", query_string={"url": YT, "rights_confirmed": "1"}).status_code == 200
    assert dirs and not any(os.path.exists(d) for d in dirs)


def test_unexpected_error_returns_text_500(client, fake_ydl):
    fake_ydl.error = RuntimeError("boom")
    response = client.get("/api/social-download", query_string={"url": YT, "rights_confirmed": "1"})
    assert response.status_code == 500
    assert "<html" not in response.get_data(as_text=True).lower()


def test_deploy_config_has_threads_and_timeout():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("Procfile", "render.yaml"):
        text = open(os.path.join(root, name)).read()
        assert "--timeout 300" in text and "gthread" in text
    assert "turboscribe" not in open(os.path.join(root, "README.md")).read().lower()
