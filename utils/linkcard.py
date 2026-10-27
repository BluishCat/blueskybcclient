"""URL のリンクカード（タイトル・説明・サムネイル）を作るためにページの OGP を読む

公式アプリは投稿時に URL のページを取得し、og:title / og:description / og:image から
プレビューカード（app.bsky.embed.external）を作っている。ここではその材料を集める。
失敗してもポスト自体は送れるよう、例外は投げずに None を返す。
"""
import io
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

_TIMEOUT_SEC = 8
_MAX_HTML_BYTES = 1024 * 1024        # <head> を読めれば十分なので先頭 1MB まで
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_THUMB_MAX_BYTES = 950 * 1000        # Bluesky のサムネイル上限（約 976KB）に余裕を持たせる
_THUMB_MAX_SIDE = 1200
_HEADERS = {
    # 一部のサイトは UA が無いと OGP を返さないため、ブラウザに近い UA を名乗る
    "User-Agent": "Mozilla/5.0 (compatible; Poisora; +https://github.com/BluishCat/poisora)",
    "Accept-Language": "ja,en;q=0.8",
}
_CHARSET_RE = re.compile(rb"""<meta[^>]+charset=["']?([A-Za-z0-9_\-]+)""", re.I)


class _MetaParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and "content" in a and key not in self.meta:
                self.meta[key] = a["content"].strip()
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def _is_http_url(url):
    return urlparse(url).scheme in ("http", "https")


def _read_limited(resp, limit):
    buf = bytearray()
    for chunk in resp.iter_content(64 * 1024):
        buf.extend(chunk)
        if len(buf) >= limit:
            break
    return bytes(buf[:limit])


def _decode_html(resp, raw):
    charset = resp.encoding if "charset" in resp.headers.get("Content-Type", "").lower() else None
    if not charset:
        m = _CHARSET_RE.search(raw[:4096])
        charset = m.group(1).decode("ascii") if m else "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _make_thumbnail(data):
    """サムネイル用に縮小・JPEG 化して上限サイズに収める。扱えない画像なら None"""
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        return None
    if img.mode not in ("RGB", "L"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        img = img.convert("RGBA")
        bg.paste(img, mask=img.split()[-1])
        img = bg
    img.thumbnail((_THUMB_MAX_SIDE, _THUMB_MAX_SIDE))
    for quality in (85, 75, 65, 50, 35):
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=quality, optimize=True)
        if out.tell() <= _THUMB_MAX_BYTES:
            return out.getvalue()
    return None


def fetch_link_card(url):
    """url のページから {"title", "description", "image"} を返す。作れなければ None

    image はサムネイル用に縮小済みの JPEG バイト列（取れなければ None）。
    """
    if not _is_http_url(url):
        return None
    try:
        with requests.get(url, headers=_HEADERS, timeout=_TIMEOUT_SEC, stream=True) as resp:
            if resp.status_code != 200 or "html" not in resp.headers.get("Content-Type", "").lower():
                return None
            html = _decode_html(resp, _read_limited(resp, _MAX_HTML_BYTES))
            page_url = resp.url
    except Exception:
        return None

    parser = _MetaParser()
    try:
        parser.feed(html)
    except Exception:
        pass
    meta = parser.meta
    title = meta.get("og:title") or meta.get("twitter:title") or parser.title.strip()
    description = meta.get("og:description") or meta.get("twitter:description") or meta.get("description") or ""
    if not title and not description:
        return None

    image = None
    image_url = meta.get("og:image") or meta.get("og:image:url") or meta.get("twitter:image")
    if image_url:
        image_url = urljoin(page_url, image_url)
        if _is_http_url(image_url):
            try:
                with requests.get(image_url, headers=_HEADERS, timeout=_TIMEOUT_SEC, stream=True) as r:
                    if r.status_code == 200:
                        image = _make_thumbnail(_read_limited(r, _MAX_IMAGE_BYTES))
            except Exception:
                image = None

    return {
        "title": " ".join(title.split())[:300],
        "description": " ".join(description.split())[:1000],
        "image": image,
    }
