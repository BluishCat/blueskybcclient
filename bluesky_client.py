import os
import sys
import traceback

def global_excepthook(exctype, value, tb):
    try:
        with open("crash_log.txt", "a") as f:
            f.write("\\n--- CRASH ---\\n")
            traceback.print_exception(exctype, value, tb, file=f)
    except:
        pass

sys.excepthook = global_excepthook
import ctypes

# Enable High DPI awareness for Windows multi-monitor setups
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

# Add current directory to path so api, models, ui modules can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.bluesky_api import BlueskyClientWrapper
from models.post import Post
from models.tab import TimelineTabModel
from utils.ui_config import save_ui_state, load_ui_state
from utils.encryption import encrypt_password, decrypt_password
from utils.paths import get_resource_path, get_app_dir
from ui.main_window import MainWindow

class BlueskyApp:
    def __init__(self):
        self.api = BlueskyClientWrapper()
        # Initial dummy tabs, will be reinitialized after login
        self.tabs = [TimelineTabModel("ホーム", "home"), TimelineTabModel("通知", "notifications")]
        
        # Persistence for read posts and hidden tabs
        self.ui_state = load_ui_state() or {}
        self.read_post_uris = set(self.ui_state.get("read_post_uris", []))
        self.bookmarked_uris = set(self.ui_state.get("bookmarked_uris", []))
        self.hidden_tabs = set(self.ui_state.get("hidden_tabs", []))
        
        self.ui = MainWindow(
            login_callback=self.handle_login,
            post_callback=self.handle_post,
            refresh_callback=self.handle_refresh,
            app_ref=self
        )

    def handle_login(self, username, password):
        return self.api.login(username, password)

    def handle_post(self, text, images=None):
        if images:
            images_bytes = []
            image_alts = []
            for img_obj in images:
                try:
                    img_raw = img_obj["data"]
                    alt = img_obj.get("alt", "")
                    if isinstance(img_raw, str): # Path from dialog/D&D
                        with open(img_raw, "rb") as f:
                            images_bytes.append(f.read())
                    elif isinstance(img_raw, dict) and "data" in img_raw: # From clipboard
                        images_bytes.append(img_raw["data"])
                    
                    image_alts.append(alt)
                except Exception as e:
                    pass; # print(f"Failed to load image: {e}")
            return self.api.send_post_with_images(text, images_bytes, image_alts=image_alts)
        return self.api.send_post(text)

    def handle_repost(self, post):
        return self.api.repost_post(post.uri, post.cid)

    def handle_like(self, post):
        return self.api.like_post(post.uri, post.cid)

    def handle_follow(self, author_handle):
        return self.api.follow_user(author_handle)

    def handle_unfollow(self, author_handle):
        return self.api.unfollow_user(author_handle)

    def handle_bookmark(self, post):
        if post.uri in self.bookmarked_uris:
            self.bookmarked_uris.remove(post.uri)
            return True, "Removed from benchmarks"
        else:
            self.bookmarked_uris.add(post.uri)
            return True, "Added to bookmarks"

    def handle_get_profile(self, handle):
        return self.api.get_user_profile(handle)

    def handle_refresh(self, tab_key=None):
        import concurrent.futures
        
        # If tab_key is provided, only refresh that tab. Otherwise refresh all.
        tabs_to_refresh = [t for t in self.tabs if t.tab_key == tab_key] if tab_key else self.tabs
        
        def _refresh_single_tab(tab):
            try:
                posts = []
                if tab.tab_type == "home":
                    feed = self.api.get_timeline(limit=30)
                    for item in feed:
                        try:
                            posts.append(Post.from_feed_view(item))
                        except Exception: pass
                elif tab.tab_type == "notifications":
                    notifs = self.api.get_notifications(limit=30)
                    for item in notifs:
                        try:
                            posts.append(Post.from_notification(item))
                        except Exception: pass
                elif tab.tab_type == "feed":
                    feed = self.api.get_feed(tab.feed_uri, limit=30)
                    for item in feed:
                        try:
                            posts.append(Post.from_feed_view(item))
                        except Exception: pass
                elif tab.tab_type == "bookmarks":
                    if self.bookmarked_uris:
                        raw_posts = self.api.get_posts(list(self.bookmarked_uris))
                        for p_view in raw_posts:
                            try:
                                class DummyFeedView:
                                    def __init__(self, p): self.post = p
                                posts.append(Post.from_feed_view(DummyFeedView(p_view)))
                            except Exception: pass
                    else:
                        posts = []
                
                for p in posts:
                    if p.uri in self.read_post_uris:
                        p.is_read = True
                
                # Check for new posts and play sound if assigned
                if posts:
                    current_top_uri = posts[0].uri
                    # Only play sound if we already have a last_top_uri (not first load) and it changed
                    if tab.last_top_uri and tab.last_top_uri != current_top_uri:
                        if tab.sound_file:
                            sound_path = get_resource_path(os.path.join("sound", tab.sound_file))
                            if os.path.exists(sound_path):
                                try:
                                    import pygame
                                    if not pygame.mixer.get_init():
                                        pygame.mixer.init()
                                    pygame.mixer.music.load(sound_path)
                                    pygame.mixer.music.play()
                                except Exception: pass
                    
                    tab.last_top_uri = current_top_uri

                tab.update_posts(posts)
                tab.error_message = None
            except Exception as e:
                tab.error_message = str(e)

        # ThreadPoolExecutor を使って各タブのAPIリクエストを並列実行
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(tabs_to_refresh)) as executor:
            executor.map(_refresh_single_tab, tabs_to_refresh)

        return self.tabs

    def reinitialize_tabs(self):
        """Fetch pinned feeds and create tabs."""
        unsorted_tabs = []
        # Always add Home and Notifications
        unsorted_tabs.append(TimelineTabModel("ホーム", "home"))
        unsorted_tabs.append(TimelineTabModel("通知", "notifications"))
        unsorted_tabs.append(TimelineTabModel("ブックマーク", "bookmarks"))

        
        # Add pinned feeds
        pinned = self.api.get_pinned_feeds()
        for f in pinned:
            unsorted_tabs.append(TimelineTabModel(f["name"], "feed", f["uri"]))
            
        # Sort based on saved order if available
        saved_order = self.ui_state.get("tab_order", [])
        if saved_order:
            # Create a map for quick lookup
            tab_map = {t.tab_key: t for t in unsorted_tabs}
            new_tabs = []
            # Add tabs in saved order
            for key in saved_order:
                if key in tab_map:
                    new_tabs.append(tab_map.pop(key))
            # Add any remaining tabs (e.g. newly pinned)
            new_tabs.extend(tab_map.values())
            self.tabs = new_tabs
        else:
            self.tabs = unsorted_tabs
            
        # Restore sounds
        tab_sounds = self.ui_state.get("tab_sounds", {})
        for t in self.tabs:
            if t.tab_key in tab_sounds:
                t.sound_file = tab_sounds[t.tab_key]
            
        return self.tabs

    def load_config(self):
        import json
        config_path = os.path.join(get_app_dir(), "config.json")
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "password" in data and data["password"]:
                    data["password"] = decrypt_password(data["password"])
                return data
        except Exception:
            return {}

    def save_config(self, username, password):
        import json
        config_path = os.path.join(get_app_dir(), "config.json")
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump({"username": username, "password": encrypt_password(password)}, f)
        except Exception as e:
            pass; # print(f"Failed to save config: {e}")

    def clear_config(self):
        config_path = os.path.join(get_app_dir(), "config.json")
        if os.path.exists(config_path):
            try:
                os.remove(config_path)
            except Exception:
                pass

    def run(self):
        while True:
            # 1. Try to load config & login automatically
            config = self.load_config()
            success = False
            username = config.get("username", "")
            password = config.get("password")
            
            if username and password:
                pass; # print("Attempting auto-login...")
                success, _ = self.api.login(username, password)
                
            if not success:
                # Show login window
                success, username, password = self.ui.show_login(default_username=username)
                if not success:
                    break # User cancelled
                
                # Save config when manual login succeeds
                self.save_config(username, password)

            # 2. Reinitialize tabs to get pinned feeds
            self.reinitialize_tabs()

            # 3. Show main window
            action = self.ui.show_main(self.tabs)
            if action == "Logout":
                self.clear_config()
                for tab in self.tabs:
                    tab.clear()
                continue # Go back to the top of the loop (login screen)
            else:
                break # Exit application

if __name__ == "__main__":
    app = BlueskyApp()
    try:
        app.run()
    except Exception as e:
        import TkEasyGUI as eg
        eg.popup(f"Fatal error: {traceback.format_exc()}", title="Error")
