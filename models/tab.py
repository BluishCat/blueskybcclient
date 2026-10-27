class TimelineTabModel:
    def __init__(self, name="Home", tab_type="home", feed_uri=None, hashtag=None):
        self.name = name
        self.tab_type = tab_type # 'home', 'notifications', 'feed', 'bookmarks', 'hashtag'
        self.feed_uri = feed_uri
        self.hashtag = hashtag  # tab_type == 'hashtag' のときのタグ（# なし）
        self.posts = []
        self.is_loading = False
        self.error_message = None
        self.sound_file = None
        self.last_top_uri = None
        
        # Generate a unique key for UI elements
        if tab_type == "home":
            self.tab_key = "home"
        elif tab_type == "notifications":
            self.tab_key = "notifications"
        elif tab_type == "bookmarks":
            self.tab_key = "bookmarks"
        elif tab_type == "hashtag":
            # タグは大文字小文字を区別しないので、キーは小文字で作る
            import hashlib
            self.tab_key = "h_" + hashlib.md5(hashtag.lower().encode()).hexdigest()[:8]
        else:
            # Use a safe version of URI or name
            import hashlib
            seed = feed_uri or name or "custom"
            self.tab_key = "f_" + hashlib.md5(seed.encode()).hexdigest()[:8]

    @classmethod
    def for_hashtag(cls, tag):
        """ハッシュタグ検索のタブ"""
        return cls(f"#{tag}", "hashtag", hashtag=tag)

    def update_posts(self, new_posts):
        # Keep track of read uris
        read_uris = {p.uri for p in self.posts if p.is_read and p.uri}
        for p in new_posts:
            if p.uri in read_uris:
                p.is_read = True
        self.posts = new_posts
        
    def clear(self):
        self.posts = []
        self.error_message = None
