import time
from urllib.parse import urlparse

import io

import requests
from atproto import Client, models

from utils import linkcard, richtext

# 動画は PDS へ直接 uploadBlob するのではなく、公式の動画サービスに先に渡してエンコードさせる。
# 直接 uploadBlob すると投稿が firehose に流れるまでエンコードが始まらず、
# 投稿直後の数秒間は視聴者側で動画が欠けて見えてしまう。
VIDEO_SERVICE_URL = "https://video.bsky.app"
VIDEO_UPLOAD_TIMEOUT_SEC = 30 * 60   # 300MB の送信を待てるだけの余裕を持たせる
VIDEO_JOB_TIMEOUT_SEC = 15 * 60      # 10分の動画のエンコード完了を待つ上限
VIDEO_JOB_POLL_INTERVAL_SEC = 1.0


def _image_aspect_ratio(data):
    """画像の縦横比。読めなければ None（その場合 Bluesky 側では正方形扱いで表示される）"""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
        return models.AppBskyEmbedDefs.AspectRatio(width=width, height=height)
    except Exception:
        return None


class BlueskyClientWrapper:
    def __init__(self):
        self.client = Client()
        self.is_logged_in = False
        self.username = None

    @property
    def did(self):
        """ログイン中アカウントの DID。ポストの URI は at://<DID>/... なので持ち主の判定に使う"""
        me = getattr(self.client, "me", None)
        return me.did if self.is_logged_in and me else None

    def login(self, handle, password):
        try:
            profile = self.client.login(handle, password)
            self.is_logged_in = True
            self.username = profile.handle
            pass; # print(f"Login successful: {self.username}")
            return True, None
        except Exception as e:
            self.is_logged_in = False
            pass; # print(f"Login failed: {e}")
            return False, str(e)

    def get_timeline(self, limit=30):
        if not self.is_logged_in:
            return []
        
        try:
            timeline = self.client.get_timeline(limit=limit)
            pass; # print(f"Timeline fetched: {len(timeline.feed)} posts")
            return timeline.feed
        except Exception as e:
            pass; # print(f"Error fetching timeline: {e}")
            return []

    def get_posts(self, uris):
        """Fetch multiple posts by their URIs."""
        if not self.is_logged_in or not uris:
            return []
        try:
            response = self.client.app.bsky.feed.get_posts({"uris": uris})
            return response.posts
        except Exception as e:
            pass; # print(f"Error fetching posts {uris}: {e}")
            return []

    def get_notifications(self, limit=30):
        if not self.is_logged_in:
            return []
            
        try:
            response = self.client.app.bsky.notification.list_notifications({'limit': limit})
            return response.notifications
        except Exception as e:
            pass; # print(f"Error fetching notifications: {e}")
            return []

    def get_pinned_feeds(self):
        """Get pinned feeds for the user."""
        if not self.is_logged_in:
            return []
        try:
            # Use raw invoke_query to bypass Pydantic validation errors caused by unknown preference types (like declaredAgePref)
            resp = self.client.invoke_query('app.bsky.actor.getPreferences')
            if not resp.success:
                pass; # print(f"Failed to fetch preferences: {resp.status_code}")
                return []
            
            prefs_data = resp.content
            if not isinstance(prefs_data, dict):
                return []
                
            pinned_feeds = []
            preferences = prefs_data.get("preferences", [])
            for pref in preferences:
                # In a raw dict, look for 'pinned' in types like app.bsky.actor.defs#savedFeedsPref or savedFeedsPrefV2
                pinned_uris = pref.get("pinned", [])
                if pinned_uris:
                    for uri in pinned_uris:
                        if isinstance(uri, str) and uri.startswith("at://"):
                            # Detect type
                            is_list = "app.bsky.graph.list" in uri
                            
                            # Get feed/list info to get the name
                            name = "Custom Feed"
                            try:
                                if is_list:
                                    info = self.client.app.bsky.graph.get_list({"list": uri})
                                    name = info.list.name
                                else:
                                    info = self.client.app.bsky.feed.get_feed_generator({"feed": uri})
                                    name = info.view.display_name
                                
                                pinned_feeds.append({
                                    "uri": uri,
                                    "name": name,
                                    "is_list": is_list
                                })
                            except Exception as e:
                                # Skip feeds that cannot be found/accessed
                                pass; # print(f"Skipping inaccessible feed {uri}: {e}")
            return pinned_feeds
        except Exception as e:
            pass; # print(f"Error fetching pinned feeds: {e}")
            return []

    def get_feed(self, feed_uri, limit=30):
        """Get posts from a specific feed URI (Generator or List)."""
        if not self.is_logged_in:
            return []
        try:
            if "app.bsky.graph.list" in feed_uri:
                response = self.client.app.bsky.feed.get_list_feed({"list": feed_uri, "limit": limit})
            else:
                response = self.client.app.bsky.feed.get_feed({"feed": feed_uri, "limit": limit})
            return response.feed
        except Exception as e:
            pass; # print(f"Error fetching feed {feed_uri}: {e}")
            return []

    def search_hashtag(self, tag, limit=30):
        """ハッシュタグ #tag の付いたポストを新しい順に返す（PostView のリスト）"""
        if not self.is_logged_in:
            return []
        response = self.client.app.bsky.feed.search_posts({"q": f"#{tag}", "sort": "latest", "limit": limit})
        return response.posts

    def send_post(self, text, reply_to_uri=None, quote_uri=None):
        """テキストのみのポスト。Returns (success, error_msg)

        reply_to_uri を渡すとそのポストへの返信、quote_uri を渡すとそのポストの引用になる。
        添付も引用も無く本文に URL があれば、そのページのリンクカードを付ける。
        """
        return self._send(text, None, reply_to_uri, quote_uri)

    def send_post_with_images(self, text, images_bytes, image_alts=None, reply_to_uri=None, quote_uri=None):
        if not images_bytes:
            return self.send_post(text, reply_to_uri=reply_to_uri, quote_uri=quote_uri)

        image_alts = list(image_alts or [])
        image_alts += [""] * (len(images_bytes) - len(image_alts))

        def make_media():
            images = [
                models.AppBskyEmbedImages.Image(
                    alt=alt or "",
                    image=self.client.upload_blob(data).blob,
                    aspect_ratio=_image_aspect_ratio(data),
                )
                for data, alt in zip(images_bytes, image_alts)
            ]
            return models.AppBskyEmbedImages.Main(images=images)

        return self._send(text, make_media, reply_to_uri, quote_uri)

    def send_post_with_video(self, text, video_bytes, filename, alt="", aspect_ratio=None, on_progress=None,
                             reply_to_uri=None, quote_uri=None):
        """動画を添付して投稿する。Returns (success, error_msg)

        aspect_ratio は (width, height)。取得できなかった場合は None を渡せば embed から省かれる。
        on_progress(state, progress) はエンコードの状態が更新されるたびに呼ばれる。
        """
        on_progress = on_progress or (lambda state, progress: None)

        def make_media():
            blob = self._upload_video(video_bytes, filename, on_progress)
            embed = models.AppBskyEmbedVideo.Main(video=blob, alt=alt)
            if aspect_ratio:
                embed.aspect_ratio = models.AppBskyEmbedDefs.AspectRatio(
                    width=aspect_ratio[0], height=aspect_ratio[1]
                )
            return embed

        return self._send(text, make_media, reply_to_uri, quote_uri)

    def _send(self, text, make_media, reply_to_uri, quote_uri):
        """返信先・引用元の確認 → 添付のアップロード → 送信、の順に行う

        返信先や引用元が消えていたら、時間のかかるアップロード（特に動画）の前に失敗させる。
        """
        if not self.is_logged_in:
            return False, "Not logged in"
        try:
            reply_to = self._build_reply_ref(reply_to_uri) if reply_to_uri else None
            quote = self._build_quote_embed(quote_uri) if quote_uri else None
            media = make_media() if make_media else None

            if media and quote:
                embed = models.AppBskyEmbedRecordWithMedia.Main(record=quote, media=media)
            else:
                embed = media or quote or self._build_link_card(text)

            self.client.send_post(text, reply_to=reply_to, embed=embed, facets=self._build_facets(text))
            return True, None
        except Exception as e:
            return False, str(e)

    def _fetch_post_view(self, uri, missing_message):
        response = self.client.app.bsky.feed.get_posts({"uris": [uri]})
        if not response.posts:
            raise RuntimeError(missing_message)
        return response.posts[0]

    def _build_reply_ref(self, parent_uri):
        """返信先ポストの URI から ReplyRef（parent と root の組）を組み立てる

        root にはスレッド先頭のポストを指定する必要がある。親自体が返信なら親のレコードに
        記録されている root を引き継ぎ、そうでなければ親がそのまま root になる。
        cid はタイムライン取得時の値を使わず送信直前に取り直す（編集・削除に追従するため）。
        """
        parent_view = self._fetch_post_view(parent_uri, "返信先のポストが見つかりません（削除された可能性があります）")
        if getattr(getattr(parent_view, "viewer", None), "reply_disabled", False):
            raise RuntimeError("このポストは返信できない設定になっています")
        parent_ref = models.create_strong_ref(parent_view)

        record = parent_view.record
        parent_reply = record.get("reply") if isinstance(record, dict) else getattr(record, "reply", None)
        if parent_reply:
            root = parent_reply["root"] if isinstance(parent_reply, dict) else parent_reply.root
            root_ref = models.ComAtprotoRepoStrongRef.Main.model_validate(root)
        else:
            root_ref = parent_ref

        return models.AppBskyFeedPost.ReplyRef(parent=parent_ref, root=root_ref)

    def _build_quote_embed(self, quote_uri):
        """引用元ポストの URI から引用の embed を組み立てる（cid は送信直前に取り直す）"""
        view = self._fetch_post_view(quote_uri, "引用元のポストが見つかりません（削除された可能性があります）")
        if getattr(getattr(view, "viewer", None), "embedding_disabled", False):
            raise RuntimeError("このポストは引用できない設定になっています")
        return models.AppBskyEmbedRecord.Main(record=models.create_strong_ref(view))

    def _build_facets(self, text):
        """本文のリンク・メンション・ハッシュタグを facets にする。無ければ None

        メンションはハンドルを DID に引けたものだけ。引けないハンドル（打ち間違いなど）は
        公式と同じくただの文字列のまま送る。
        """
        facets = []
        for span in richtext.detect(text):
            if span.kind == "link":
                feature = models.AppBskyRichtextFacet.Link(uri=span.value)
            elif span.kind == "tag":
                feature = models.AppBskyRichtextFacet.Tag(tag=span.value)
            else:
                did = self._resolve_handle(span.value)
                if not did:
                    continue
                feature = models.AppBskyRichtextFacet.Mention(did=did)
            facets.append(models.AppBskyRichtextFacet.Main(
                index=models.AppBskyRichtextFacet.ByteSlice(byte_start=span.start, byte_end=span.end),
                features=[feature],
            ))
        return facets or None

    def _resolve_handle(self, handle):
        try:
            return self.client.resolve_handle(handle).did
        except Exception:
            return None

    def _build_link_card(self, text):
        """本文の最初の URL からリンクカードの embed を作る。作れなければ None（投稿は続ける）"""
        url = richtext.first_link(text)
        if not url:
            return None
        card = linkcard.fetch_link_card(url)
        if not card:
            return None
        thumb = None
        if card["image"]:
            try:
                thumb = self.client.upload_blob(card["image"]).blob
            except Exception:
                thumb = None
        return models.AppBskyEmbedExternal.Main(external=models.AppBskyEmbedExternal.External(
            uri=url, title=card["title"], description=card["description"], thumb=thumb,
        ))

    def _upload_video(self, video_bytes, filename, on_progress):
        """動画サービスに動画を渡し、エンコード完了後の BlobRef を返す"""
        # 動画サービスは受け取った動画を利用者の代理で PDS へ uploadBlob する。
        # そのため lxm には uploadVideo ではなく com.atproto.repo.uploadBlob を指定する。
        pds_host = urlparse(self.client._base_url).netloc
        auth = self.client.com.atproto.server.get_service_auth({
            "aud": f"did:web:{pds_host}",
            "lxm": "com.atproto.repo.uploadBlob",
            "exp": int(time.time()) + 30 * 60,
        })

        response = requests.post(
            f"{VIDEO_SERVICE_URL}/xrpc/app.bsky.video.uploadVideo",
            params={"did": self.client.me.did, "name": filename},
            headers={"Authorization": f"Bearer {auth.token}", "Content-Type": "video/mp4"},
            data=video_bytes,
            timeout=VIDEO_UPLOAD_TIMEOUT_SEC,
        )
        job = self._read_job_status(response)
        blob = job.get("blob")
        deadline = time.time() + VIDEO_JOB_TIMEOUT_SEC

        while not blob:
            on_progress(job.get("state"), job.get("progress"))

            if job.get("state") == "JOB_STATE_FAILED":
                raise RuntimeError(job.get("message") or job.get("failureCode") or "動画の処理に失敗しました")
            if time.time() > deadline:
                raise RuntimeError("動画の処理が時間内に終わりませんでした")

            time.sleep(VIDEO_JOB_POLL_INTERVAL_SEC)
            status = requests.get(
                f"{VIDEO_SERVICE_URL}/xrpc/app.bsky.video.getJobStatus",
                params={"jobId": job["jobId"]},
                timeout=30,
            )
            job = self._read_job_status(status)
            blob = job.get("blob")

        on_progress(job.get("state"), job.get("progress"))
        return models.blob_ref.BlobRef.model_validate(blob)

    def _read_job_status(self, response):
        """動画サービスの応答から jobStatus を取り出す

        同じ動画が処理済みの場合は already_exists のエラー応答になるが、
        その中に過去の BlobRef が入っているため、成否に関わらず中身を先に見る。
        """
        try:
            body = response.json()
        except ValueError:
            raise RuntimeError(f"動画サービスが予期しない応答を返しました (HTTP {response.status_code})")

        job = body.get("jobStatus") or body
        if job.get("blob") or job.get("jobId"):
            return job

        raise RuntimeError(body.get("message") or body.get("error") or f"HTTP {response.status_code}")

    def delete_post(self, uri):
        """自分のポストを削除する。Returns (success, error_msg)"""
        if not self.is_logged_in:
            return False, "Not logged in"
        try:
            if not self.client.delete_post(uri):
                return False, "削除に失敗しました"
            return True, None
        except Exception as e:
            return False, str(e)

    def like_post(self, uri, cid):
        """Like a post. Returns (success, error_msg, like_record_uri)."""
        if not self.is_logged_in:
            return False, "Not logged in", None
        try:
            resp = self.client.like(uri=uri, cid=cid)
            return True, None, resp.uri
        except Exception as e:
            return False, str(e), None

    def unlike_post(self, like_uri):
        """いいね（お気に入り）を取り消す。like_uri はいいねレコードの URI。Returns (success, error_msg)"""
        if not self.is_logged_in:
            return False, "Not logged in"
        try:
            self.client.unlike(like_uri)
            return True, None
        except Exception as e:
            return False, str(e)

    def repost_post(self, uri, cid):
        """Repost a post. Returns (success, error_msg, repost_record_uri)."""
        if not self.is_logged_in:
            return False, "Not logged in", None
        try:
            resp = self.client.repost(uri=uri, cid=cid)
            return True, None, resp.uri
        except Exception as e:
            return False, str(e), None

    def unrepost_post(self, repost_uri):
        """リポストを取り消す。repost_uri はリポストレコードの URI。Returns (success, error_msg)"""
        if not self.is_logged_in:
            return False, "Not logged in"
        try:
            self.client.unrepost(repost_uri)
            return True, None
        except Exception as e:
            return False, str(e)

    def follow_user(self, handle_or_did):
        """Follow a user. Returns (success, error_msg)."""
        if not self.is_logged_in:
            return False, "Not logged in"
        try:
            profile = self.client.get_profile(handle_or_did)
            if profile.viewer and profile.viewer.following:
                return False, "Already following"
            
            self.client.follow(profile.did)
            return True, None
        except Exception as e:
            pass; # print(f"Error following user: {e}")
            return False, str(e)

    def unfollow_user(self, handle_or_did):
        """Unfollow a user. Returns (success, error_msg)."""
        if not self.is_logged_in:
            return False, "Not logged in"
        try:
            profile = self.client.get_profile(handle_or_did)
            if getattr(profile, 'viewer', None) and getattr(profile.viewer, 'following', None):
                self.client.unfollow(profile.viewer.following)
                return True, None
            else:
                return False, "Not following"
        except Exception as e:
            pass; # print(f"Error unfollowing user: {e}")
            return False, str(e)

    def get_user_profile(self, handle_or_did):
        """Get user profile details."""
        if not self.is_logged_in:
            return None
        try:
            return self.client.get_profile(handle_or_did)
        except Exception as e:
            pass; # print(f"Error getting profile: {e}")
            return None

