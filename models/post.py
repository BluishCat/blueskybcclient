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

class Post:
    def __init__(self, author_handle, author_display_name, text, created_at, uri=None, cid=None, reply_to=None, thumbnail_urls=None, full_image_urls=None, repost_count=0, like_count=0, avatar_url=None, reply_parent_author=None, reply_parent_handle=None, reply_parent_text=None, quote_author=None, quote_handle=None, quote_text=None, is_repost=False, reposted_by_author=None, reposted_by_handle=None, is_follower=False):
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
        self.repost_count = repost_count or 0
        self.like_count = like_count or 0
        self.avatar_url = avatar_url
        self.reply_parent_author = reply_parent_author
        self.reply_parent_handle = reply_parent_handle
        self.reply_parent_text = reply_parent_text
        self.quote_author = quote_author
        self.quote_handle = quote_handle
        self.quote_text = quote_text
        self.is_follower = is_follower

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
        
        # Extract thumbnail and full image if any
        thumbnail_urls = []
        full_image_urls = []
        quote_author = None
        quote_handle = None
        quote_text = None
        if hasattr(post, 'embed') and post.embed:
            # Extract quote info
            embed = post.embed
            if hasattr(embed, 'record'):
                rec = embed.record
                if hasattr(rec, 'record'):
                    view_rec = rec.record
                    if hasattr(view_rec, 'value') and hasattr(view_rec.value, 'text'):
                        quote_text = view_rec.value.text
                    elif hasattr(view_rec, 'text'):
                        quote_text = view_rec.text
                    if hasattr(view_rec, 'author'):
                        quote_handle = view_rec.author.handle
                        quote_author = view_rec.author.display_name or view_rec.author.handle
                elif hasattr(rec, 'value'):
                    if hasattr(rec.value, 'text'):
                        quote_text = rec.value.text
                    if hasattr(rec, 'author'):
                        quote_handle = rec.author.handle
                        quote_author = getattr(rec.author, 'display_name', '') or rec.author.handle
            elif hasattr(embed, 'media') and hasattr(embed, 'record'):
                if hasattr(embed.record, 'record'):
                    view_rec = embed.record.record
                    if hasattr(view_rec, 'value') and hasattr(view_rec.value, 'text'):
                        quote_text = view_rec.value.text
                    elif hasattr(view_rec, 'text'):
                        quote_text = view_rec.text
                    if hasattr(view_rec, 'author'):
                        quote_handle = view_rec.author.handle
                        quote_author = view_rec.author.display_name or view_rec.author.handle

            # Check for images
            # models.AppBskyEmbedImages.View
            if hasattr(post.embed, 'images') and post.embed.images:
                for img in post.embed.images:
                    if img.thumb:
                        thumbnail_urls.append(img.thumb)
                    if img.fullsize:
                        full_image_urls.append(img.fullsize)
            # Check for external (link preview)
            elif hasattr(post.embed, 'external') and hasattr(post.embed.external, 'thumb'):
                thumb = post.embed.external.thumb
                if thumb:
                    thumbnail_urls.append(thumb)
                    if hasattr(post.embed.external, 'uri') and post.embed.external.uri:
                        full_image_urls.append(post.embed.external.uri)
                
        avatar_url = getattr(author, 'avatar', None)
        return cls(
            author_handle=author.handle,
            author_display_name=author.display_name,
            text=text,
            created_at=created_at,
            uri=post.uri,
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
            is_follower=is_follower
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
            reply_to=None,
            thumbnail_urls=[],
            full_image_urls=[],
            avatar_url=getattr(author, 'avatar', None),
            is_follower=is_follower
        )

    def __str__(self):
        return f"{self.author_display_name} (@{self.author_handle})\n{self.text}\n{self.created_at}"
