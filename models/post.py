from datetime import datetime, timezone
import sys

def format_bluesky_date(date_str):
    if not date_str:
        return ""
    try:
        # e.g., '2023-11-26T14:48:00.000Z'
        # Remove the 'Z' and parse as naive, then replace tzinfo to UTC
        if date_str.endswith('Z'):
            date_str = date_str[:-1]
        
        # Sometimes there's higher ms precision or timezone offsets
        if '+' in date_str:
            dt = datetime.fromisoformat(date_str)
        else:
            # Handle fractional seconds precision issues by stripping them if necessary
            if '.' in date_str:
                dt = datetime.fromisoformat(date_str)
            else:
                dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S")
            dt = dt.replace(tzinfo=timezone.utc)
            
        # Convert to local timezone
        local_dt = dt.astimezone()
        return local_dt.strftime("%Y/%m/%d %H:%M:%S")
    except Exception:
        # パース不能な日時はそのまま返す
        return date_str

def _facet_spans(text, facets):
    """record.facets（UTF-8 のバイト位置）を本文の文字位置の範囲にする

    Returns [(開始文字位置, 終了文字位置, 種類, 値)]。種類は link / mention / tag、
    値はそれぞれ URL / DID / タグ（# なし）。位置が文字の途中を指すものや重なるものは捨てる。
    """
    if not text or not facets:
        return []
    byte_to_char = {}
    pos = 0
    for i, ch in enumerate(text):
        byte_to_char[pos] = i
        pos += len(ch.encode("utf-8"))
    byte_to_char[pos] = len(text)

    spans = []
    for facet in facets:
        try:
            index = facet.index
            start = byte_to_char.get(index.byte_start)
            end = byte_to_char.get(index.byte_end)
            if start is None or end is None or start >= end:
                continue
            for feature in facet.features:
                if getattr(feature, 'uri', None):
                    spans.append((start, end, 'link', feature.uri))
                elif getattr(feature, 'did', None):
                    spans.append((start, end, 'mention', feature.did))
                elif getattr(feature, 'tag', None):
                    spans.append((start, end, 'tag', feature.tag))
                else:
                    continue
                break
        except Exception:
            continue
    spans.sort()
    result = []
    for span in spans:
        if result and span[0] < result[-1][1]:
            continue
        result.append(span)
    return result


def _quoted_view(embed):
    """埋め込みから引用元ポストのビュー (app.bsky.embed.record#viewRecord) を取り出す。無ければ None

    引用のみ: embed.record が viewRecord
    引用＋画像など (recordWithMedia): embed.record.record が viewRecord
    削除済み・ブロック中の引用元（viewNotFound / viewBlocked）やフィード・リストの埋め込みは value が無いので None。
    """
    rec = getattr(embed, 'record', None)
    if rec is None:
        return None
    if getattr(rec, 'value', None) is None and getattr(rec, 'record', None) is not None:
        rec = rec.record
    value = getattr(rec, 'value', None)
    if value is None or not hasattr(value, 'text'):
        return None
    return rec


def _media_urls(media):
    """添付のビューから (サムネイル URL 群, 拡大表示・リンク先 URL 群, 動画か) を取り出す"""
    thumbs, fulls = [], []
    # models.AppBskyEmbedImages.View
    if getattr(media, 'images', None):
        for img in media.images:
            if img.thumb:
                thumbs.append(img.thumb)
            if img.fullsize:
                fulls.append(img.fullsize)
        return thumbs, fulls, False
    # 動画 (app.bsky.embed.video#view) の実体は HLS のため再生できない。
    # サムネイルの静止画だけを表示し、再生はブラウザの投稿ページに任せる
    if hasattr(media, 'playlist'):
        if media.thumbnail:
            thumbs.append(media.thumbnail)
        return thumbs, fulls, True
    # リンクカード
    external = getattr(media, 'external', None)
    if external is not None and getattr(external, 'thumb', None):
        thumbs.append(external.thumb)
        if getattr(external, 'uri', None):
            fulls.append(external.uri)
    return thumbs, fulls, False


class Post:
    def __init__(self, author_handle, author_display_name, text, created_at, uri=None, cid=None, reply_to=None, thumbnail_urls=None, full_image_urls=None, repost_count=0, like_count=0, avatar_url=None, reply_parent_author=None, reply_parent_handle=None, reply_parent_text=None, quote_author=None, quote_handle=None, quote_text=None, is_repost=False, reposted_by_author=None, reposted_by_handle=None, is_follower=False, is_video=False, facet_spans=None, media_from_quote=False, quote_created_at=None, viewer_like_uri=None, viewer_repost_uri=None, reply_disabled=False, embedding_disabled=False):
        self.author_handle = author_handle
        self.author_display_name = author_display_name or author_handle
        self.text = text
        self.created_at = format_bluesky_date(created_at)
        self.uri = uri
        self.cid = cid
        self.reply_to = reply_to
        self.is_read = False
        self.is_repost = is_repost
        self.reposted_by_author = reposted_by_author
        self.reposted_by_handle = reposted_by_handle
        self.thumbnail_urls = thumbnail_urls or []
        self.full_image_urls = full_image_urls or []
        self.is_video = is_video
        # 本文中のリンク・メンション・タグ（_facet_spans の戻り値）。詳細ペインで押せるようにする
        self.facet_spans = facet_spans or []
        self.repost_count = repost_count or 0
        self.like_count = like_count or 0
        self.avatar_url = avatar_url
        self.reply_parent_author = reply_parent_author
        self.reply_parent_handle = reply_parent_handle
        self.reply_parent_text = reply_parent_text
        self.quote_author = quote_author
        self.quote_handle = quote_handle
        self.quote_text = quote_text
        self.quote_created_at = format_bluesky_date(quote_created_at) if quote_created_at else ""
        # thumbnail_urls 等が引用元の添付か（自分の添付が無い引用ポストのとき True）
        self.media_from_quote = media_from_quote
        self.is_follower = is_follower
        # 自分との関係（ログイン中アカウントのいいね・リポストのレコード URI、返信・引用の可否）
        self.viewer_like_uri = viewer_like_uri
        self.viewer_repost_uri = viewer_repost_uri
        self.reply_disabled = reply_disabled
        self.embedding_disabled = embedding_disabled

    @classmethod
    def from_feed_view(cls, feed_view_post):
        post = feed_view_post.post
        record = post.record
        
        # Check if it's a repost
        is_repost = False
        reposted_by_author = None
        reposted_by_handle = None
        if hasattr(feed_view_post, 'reason') and feed_view_post.reason:
            reason_type = type(feed_view_post.reason).__name__
            if "ReasonRepost" in reason_type:
                is_repost = True
                try:
                    reposter = feed_view_post.reason.by
                    reposted_by_handle = reposter.handle
                    reposted_by_author = getattr(reposter, 'display_name', '') or reposter.handle
                except Exception:
                    pass
        
        # atproto models.AppBskyFeedPost.Record
        text = getattr(record, 'text', '')
        created_at = getattr(record, 'created_at', '')
        
        author = post.author
        is_follower = False
        if hasattr(author, 'viewer') and author.viewer:
            if hasattr(author.viewer, 'followed_by') and author.viewer.followed_by:
                is_follower = True
        
        # Extract reply info if any
        reply_to = None
        reply_parent_author = None
        reply_parent_handle = None
        reply_parent_text = None
        if hasattr(feed_view_post, 'reply') and feed_view_post.reply:
            try:
                parent = feed_view_post.reply.parent
                if hasattr(parent, 'author'):
                    reply_to = parent.author.handle
                    reply_parent_handle = parent.author.handle
                    reply_parent_author = parent.author.display_name or parent.author.handle
                if hasattr(parent, 'record'):
                    reply_parent_text = getattr(parent.record, 'text', '')
            except Exception:
                pass
        
        # 添付（画像・動画・リンクカード）と引用元
        thumbnail_urls = []
        full_image_urls = []
        is_video = False
        media_from_quote = False
        quote_author = None
        quote_handle = None
        quote_text = None
        quote_created_at = None
        embed = getattr(post, 'embed', None)
        if embed:
            quoted = _quoted_view(embed)
            if quoted is not None:
                value = getattr(quoted, 'value', None)
                quote_text = getattr(value, 'text', None) or ''
                quote_created_at = getattr(value, 'created_at', None)
                q_author = getattr(quoted, 'author', None)
                if q_author is not None:
                    quote_handle = q_author.handle
                    quote_author = getattr(q_author, 'display_name', '') or q_author.handle

            # 引用付き投稿 (recordWithMedia) では画像・動画は embed.media 側にぶら下がる
            thumbnail_urls, full_image_urls, is_video = _media_urls(getattr(embed, 'media', embed))

            # 自分の添付が無い引用ポストは、引用元の画像・動画・リンクカードを見せる
            if not thumbnail_urls and quoted is not None:
                for q_embed in getattr(quoted, 'embeds', None) or []:
                    thumbnail_urls, full_image_urls, is_video = _media_urls(getattr(q_embed, 'media', q_embed))
                    if thumbnail_urls:
                        media_from_quote = True
                        break

        avatar_url = getattr(author, 'avatar', None)
        viewer = getattr(post, 'viewer', None)
        return cls(
            author_handle=author.handle,
            author_display_name=author.display_name,
            text=text,
            created_at=created_at,
            uri=post.uri,
            cid=post.cid,
            reply_to=reply_to,
            thumbnail_urls=thumbnail_urls,
            full_image_urls=full_image_urls,
            repost_count=getattr(post, 'repost_count', 0),
            like_count=getattr(post, 'like_count', 0),
            avatar_url=avatar_url,
            is_repost=is_repost,
            reposted_by_author=reposted_by_author,
            reposted_by_handle=reposted_by_handle,
            reply_parent_author=reply_parent_author,
            reply_parent_handle=reply_parent_handle,
            reply_parent_text=reply_parent_text,
            quote_author=quote_author,
            quote_handle=quote_handle,
            quote_text=quote_text,
            quote_created_at=quote_created_at,
            media_from_quote=media_from_quote,
            is_follower=is_follower,
            is_video=is_video,
            facet_spans=_facet_spans(text, getattr(record, 'facets', None)),
            viewer_like_uri=getattr(viewer, 'like', None),
            viewer_repost_uri=getattr(viewer, 'repost', None),
            reply_disabled=bool(getattr(viewer, 'reply_disabled', False)),
            embedding_disabled=bool(getattr(viewer, 'embedding_disabled', False)),
        )

    @classmethod
    def from_notification(cls, notification):
        record = notification.record
        text = getattr(record, 'text', f"[{notification.reason}]")
        created_at = getattr(record, 'created_at', '')
        
        author = notification.author
        is_follower = False
        if hasattr(author, 'viewer') and author.viewer:
            if hasattr(author.viewer, 'followed_by') and author.viewer.followed_by:
                is_follower = True
        
        return cls(
            author_handle=author.handle,
            author_display_name=author.display_name,
            text=text,
            created_at=created_at,
            uri=notification.uri,
            cid=notification.cid,
            reply_to=None,
            thumbnail_urls=[],
            full_image_urls=[],
            avatar_url=getattr(author, 'avatar', None),
            is_follower=is_follower,
            facet_spans=_facet_spans(text, getattr(record, 'facets', None)),
        )

    @property
    def is_post(self):
        """返信できる実体のポストか。いいね・リポスト・フォロー通知の URI はポストではない"""
        return bool(self.uri) and "/app.bsky.feed.post/" in self.uri

    @property
    def can_reply(self):
        return self.is_post and not self.reply_disabled

    @property
    def can_quote(self):
        return self.is_post and not self.embedding_disabled

    def __str__(self):
        return f"{self.author_display_name} (@{self.author_handle})\n{self.text}\n{self.created_at}"
