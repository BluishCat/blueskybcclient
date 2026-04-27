from atproto import Client, models

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

