"""ポスト本文からリンク・メンション・ハッシュタグを見つけ、facets 用の範囲を返す

Bluesky の facets は本文中の範囲を UTF-8 のバイト位置で指定する。atproto ライブラリの
send_post は文字列を渡すと facets を作らないため、自前で検出しないとリンクにならない。

検出規則は公式クライアント（@atproto/api の RichText.detectFacets）に合わせているが、
URL だけは日本語の直後でも拾う（「詳細はhttps://…」のように空白を入れずに書くことが多いため）。
その代わり URL は ASCII 文字の範囲で打ち切り、直後に続く日本語を巻き込まないようにしている。
"""
import re
import unicodedata
from dataclasses import dataclass

# 直前が英数字・記号の途中でなければ拾う。URL 本体は空白と全角文字の手前まで
_URL_RE = re.compile(r"(?<![A-Za-z0-9_@])https?://[!-~]+")
# URL 末尾の句読点は文の一部とみなして外す（公式と同じ扱い）
_URL_TRAILING = ".,;:!?'\""

# メンション: 行頭・空白・開き括弧の直後の @handle
_MENTION_RE = re.compile(r"(?:^|(?<=[\s(（]))@([a-zA-Z0-9.-]+)")
_HANDLE_RE = re.compile(
    r"^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$"
)

# ハッシュタグ: 行頭・空白の直後の # / ＃ から空白の手前まで
_TAG_RE = re.compile(r"(?:^|(?<=\s))[#＃]([^\s\u00AD\u2060\u200A\u200B\u200C\u200D\u20e2]+)")
_TAG_MAX_LEN = 64


@dataclass
class Span:
    kind: str    # "link" / "mention" / "tag"
    start: int   # UTF-8 バイト位置（含む）
    end: int     # UTF-8 バイト位置（含まない）
    value: str   # link: URL / mention: ハンドル / tag: # を除いたタグ


def _byte_pos(text, index):
    return len(text[:index].encode("utf-8"))


def _is_punct(ch):
    return unicodedata.category(ch).startswith("P")


def _find_links(text):
    for m in _URL_RE.finditer(text):
        url = m.group(0)
        while url:
            if url[-1] in _URL_TRAILING:
                url = url[:-1]
            elif url[-1] == ")" and "(" not in url:
                url = url[:-1]
            else:
                break
        if len(url) <= len("https://"):
            continue
        yield m.start(), m.start() + len(url), url


def _find_mentions(text):
    for m in _MENTION_RE.finditer(text):
        handle = m.group(1).rstrip(".")
        if not _HANDLE_RE.match(handle):
            continue
        start = m.start(1) - 1  # 先頭の @ を含める
        yield start, m.start(1) + len(handle), handle.lower()


def _find_tags(text):
    for m in _TAG_RE.finditer(text):
        tag = m.group(1)
        if tag.startswith("\ufe0f"):  # キーキャップ絵文字 #️⃣
            continue
        while tag and _is_punct(tag[-1]):
            tag = tag[:-1]
        # 数字と記号だけのもの（#1 など）はタグにしない
        if not tag or len(tag) > _TAG_MAX_LEN or all(c.isdigit() or _is_punct(c) for c in tag):
            continue
        start = m.start(1) - 1  # 先頭の # を含める
        yield start, m.start(1) + len(tag), tag


def detect(text):
    """本文中のリンク・メンション・タグを Span のリストで返す（位置順・重なりなし）

    重なった場合は URL を優先する（URL 中の # や @ をタグ・メンションにしない）。
    """
    if not text:
        return []
    found = []
    for kind, finder in (("link", _find_links), ("mention", _find_mentions), ("tag", _find_tags)):
        for start, end, value in finder(text):
            if any(start < e and s < end for s, e, _, _ in found):
                continue
            found.append((start, end, kind, value))
    found.sort()
    return [Span(kind, _byte_pos(text, s), _byte_pos(text, e), v) for s, e, kind, v in found]


def first_link(text):
    """本文中で最初に出てくる URL（リンクカード用）。無ければ None"""
    for span in detect(text):
        if span.kind == "link":
            return span.value
    return None
