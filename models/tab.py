class TimelineTabModel:
    def __init__(self, name="Home", tab_type="home", feed_uri=None):
        self.name = name
        self.tab_type = tab_type # 'home', 'notifications', 'feed'
        self.feed_uri = feed_uri
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
        else:
            # Use a safe version of URI or name
            import hashlib
            seed = feed_uri or name or "custom"
            self.tab_key = "f_" + hashlib.md5(seed.encode()).hexdigest()[:8]

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
