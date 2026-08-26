import time
from urllib.parse import urlparse

import requests
from atproto import Client, models

# 動画は PDS へ直接 uploadBlob するのではなく、公式の動画サービスに先に渡してエンコードさせる。
# 直接 uploadBlob すると投稿が firehose に流れるまでエンコードが始まらず、
# 投稿直後の数秒間は視聴者側で動画が欠けて見えてしまう。
VIDEO_SERVICE_URL = "https://video.bsky.app"
VIDEO_UPLOAD_TIMEOUT_SEC = 30 * 60   # 300MB の送信を待てるだけの余裕を持たせる
VIDEO_JOB_TIMEOUT_SEC = 15 * 60      # 10分の動画のエンコード完了を待つ上限
VIDEO_JOB_POLL_INTERVAL_SEC = 1.0


class BlueskyClientWrapper:
    def __init__(self):
        self.client = Client()
        self.is_logged_in = False
        self.username = None

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

    def send_post(self, text):
        if not self.is_logged_in:
            return False, "Not logged in"
        
        try:
            self.client.send_post(text)
            return True, None
        except Exception as e:
            return False, str(e)

    def send_post_with_images(self, text, images_bytes, image_alts=None):
        if not self.is_logged_in:
            return False, "Not logged in"
            
        if not images_bytes:
            return self.send_post(text)
            
        if not image_alts:
            image_alts = [""] * len(images_bytes)
            
        try:
            self.client.send_images(text=text, images=images_bytes, image_alts=image_alts)
            return True, None
        except Exception as e:
            return False, str(e)

    def send_post_with_video(self, text, video_bytes, filename, alt="", aspect_ratio=None, on_progress=None):
        """動画を添付して投稿する。Returns (success, error_msg)

        aspect_ratio は (width, height)。取得できなかった場合は None を渡せば embed から省かれる。
        on_progress(state, progress) はエンコードの状態が更新されるたびに呼ばれる。
        """
        if not self.is_logged_in:
            return False, "Not logged in"

        on_progress = on_progress or (lambda state, progress: None)

        try:
            blob = self._upload_video(video_bytes, filename, on_progress)

            embed = models.AppBskyEmbedVideo.Main(video=blob, alt=alt)
            if aspect_ratio:
                embed.aspect_ratio = models.AppBskyEmbedDefs.AspectRatio(
                    width=aspect_ratio[0], height=aspect_ratio[1]
                )

            self.client.send_post(text, embed=embed)
            return True, None
        except Exception as e:
            return False, str(e)

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

    def like_post(self, uri, cid):
        """Like a post. Returns (success, error_msg)."""
        if not self.is_logged_in:
            return False, "Not logged in"
        try:
            self.client.like(uri=uri, cid=cid)
            return True, None
        except Exception as e:
            pass; # print(f"Error liking post: {e}")
            return False, str(e)

    def repost_post(self, uri, cid):
        """Repost a post. Returns (success, error_msg)."""
        if not self.is_logged_in:
            return False, "Not logged in"
        try:
            self.client.repost(uri=uri, cid=cid)
            return True, None
        except Exception as e:
            pass; # print(f"Error reposting post: {e}")
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

