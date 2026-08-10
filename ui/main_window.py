import TkEasyGUI as eg
import logging
import io
import os
import uuid
import tkinter as tk
from tkinter import ttk, font as tkfont, filedialog, messagebox
from typing import Optional
import requests
import io
import threading
import webbrowser
import queue
import os
import uuid
from collections import OrderedDict
import windnd
from PIL import Image as PILImage, ImageTk as PILImageTk, ImageGrab
from utils.ui_config import save_ui_state, load_ui_state
from utils.paths import get_resource_path
from utils.version import __version__

# ウィンドウタイトルの共通部分。ログイン後の統計付きタイトルも同じ前半を使う
APP_TITLE = f"Bluesky BC Client v{__version__}"

# 未読ジャンプ長押しの送り間隔。KeyPress/KeyRelease で押下状態を直接見て
# after() で送るため、OSのキーリピート速度にもイベントキューの往復にも縛られない。
# 16ms は Tk のタイマー分解能と画面のリフレッシュ(60Hz)から見た実質的な下限。
# これ以上詰めても描画が追いつかないので、残る速度は1投稿の処理時間で決まる。
SPACE_REPEAT_INTERVAL_MS = 16
# 長押しと単押しを分ける待ち。これを超えて押され続けたときだけ連続送りに入る。
SPACE_REPEAT_DELAY_MS = 300
# 詳細ペインを本描画するまでの待ち。通常はキーを離した時点で即描画するので、
# フォーカス喪失などで KeyRelease を取りこぼした場合の保険として働く。
DETAIL_RENDER_SETTLE_MS = 250
# Windows Tkinter は全角スペースやIME経由のキーに '??' を返すことがある。
SPACE_KEYSYMS = ("space", " ", "　", "??")

# --- TkEasyGUI compatibility fix for version 1.0.40 ---
# TkEasyGUI's _widget_update() stores all kwargs (including `visible`) into
# self.props. When that element object is later used to create a widget,
# tk.Frame(parent, **self.props) crashes because Tkinter has no `visible` option.
# Fix: strip `visible` from props inside prepare_create() before widget creation.
_orig_prepare_create = eg.Element.prepare_create
def _patched_prepare_create(self, win):
    self.props.pop('visible', None)
    self.props.pop('element_justification', None)
    _orig_prepare_create(self, win)
eg.Element.prepare_create = _patched_prepare_create

def _set_elem_visible(elem, visible: bool):
    """Show or hide a TkEasyGUI element using pack_forget/pack.
    Bypasses TkEasyGUI's broken update(visible=..) which tries to pass
    `visible` as a Tkinter config option (not supported by tk.Frame etc.).
    """
    try:
        widget = elem.widget
        if widget is None:
            return
        if visible:
            # Restore with essential pack info (safer than full dict with 'in')
            try:
                pack_options = {}
                if hasattr(elem, '_pack_side'):
                    pack_options['side'] = elem._pack_side
                if hasattr(elem, '_pack_fill'):
                    pack_options['fill'] = elem._pack_fill
                if hasattr(elem, '_pack_expand'):
                    pack_options['expand'] = elem._pack_expand
                widget.pack(**pack_options)
            except Exception as e:
                pass
        else:
            # Save current pack info before hiding
            try:
                info = widget.pack_info()
                elem._pack_side = info.get('side', 'left')
                elem._pack_fill = info.get('fill', 'none')
                elem._pack_expand = info.get('expand', 0)
            except Exception:
                elem._pack_side = 'left'
            widget.pack_forget()
    except Exception as e:
        pass

class PanedColumn(eg.Element):
    """Custom element to support dragging between two layouts using ttk.Panedwindow."""
    def __init__(self, layout1, layout2, key="-PANED-", orient="vertical", weight1=3, weight2=1, minsize1=0, minsize2=0, **kw):
        super().__init__("PanedColumn", "TPanedwindow", key, True, **kw)
        self.has_children = False # We handle recursion manually in create()
        self.layout = [] # Satisfy any internal checks
        self.layout1 = layout1
        self.layout2 = layout2
        self.orient = orient
        self.weight1 = weight1
        self.weight2 = weight2
        self.minsize1 = minsize1
        self.minsize2 = minsize2

    def create(self, win: eg.Window, parent: tk.Widget) -> tk.Widget:
        # Use ttk.Panedwindow for the draggable behavior
        self.widget = ttk.Panedwindow(parent, orient=self.orient)
        
        # Create first pane
        if self.orient == "vertical":
            self.frame1 = ttk.Frame(self.widget, height=self.minsize1)
        else:
            self.frame1 = ttk.Frame(self.widget, width=self.minsize1)
        self.widget.add(self.frame1, weight=self.weight1)
        win._create_widget(self.frame1, self.layout1)
        
        # Force the row frames to expand and fill the pane
        for child in self.frame1.winfo_children():
            # TkEasyGUI sometimes binds options loosely, we must repack forcefully
            try:
                pack_info = child.pack_info()
                child.pack(expand=True, fill="both", padx=pack_info.get('padx', 0), pady=pack_info.get('pady', 0))
            except Exception:
                child.pack(expand=True, fill="both")
        
        # Create second pane
        if self.orient == "vertical":
            self.frame2 = ttk.Frame(self.widget, height=self.minsize2)
        else:
            self.frame2 = ttk.Frame(self.widget, width=self.minsize2)
        self.widget.add(self.frame2, weight=self.weight2)
        win._create_widget(self.frame2, self.layout2)
        
        for child in self.frame2.winfo_children():
            try:
                pack_info = child.pack_info()
                child.pack(expand=True, fill="both", padx=pack_info.get('padx', 0), pady=pack_info.get('pady', 0))
            except Exception:
                child.pack(expand=True, fill="both")
        
        # TkEasyGUIはカスタム Element に expand=True を適用しない場合があるので、
        # create() 完了後に after(1) で自分自身を pack_configure する
        _self_widget = self.widget
        def _fix_pack_self():
            try:
                _self_widget.pack_configure(expand=True, fill="both")
                if _self_widget.master:
                    _self_widget.master.pack_configure(expand=True, fill="both")
            except Exception:
                pass
        try:
            parent.after(1, _fix_pack_self)
        except Exception:
            pass
        
        return self.widget
    
    def set_pane2_visible(self, visible):
        """Toggle second pane visibility."""
        if not hasattr(self, "widget") or not self.widget:
            return
        panes = [str(p) for p in self.widget.panes()]
        is_visible = str(self.frame2) in panes
        
        if visible and not is_visible:
            # Show pane - restore at the end
            self.widget.add(self.frame2, weight=self.weight2)
            # Ensure it is actually displayed by updating the panedwindow
            self.widget.update_idletasks()
            if hasattr(self, "_saved_sash_pos") and self._saved_sash_pos is not None:
                try:
                    self.widget.sashpos(0, self._saved_sash_pos)
                except Exception:
                    pass
        elif not visible and is_visible:
            # Hide pane
            try:
                self._saved_sash_pos = self.widget.sashpos(0)
            except Exception:
                pass
            self.widget.forget(self.frame2)

class TabReorderer:
    """Helper class to enable reordering of tabs in a ttk.Notebook."""
    def __init__(self, notebook: ttk.Notebook, on_reorder_callback):
        self.notebook = notebook
        self.on_reorder_callback = on_reorder_callback
        self.notebook.bind("<Button-1>", self.on_press, add="+")
        self.notebook.bind("<B1-Motion>", self.on_drag, add="+")
        self.drag_data = {"tab_id": None, "index": None}

    def on_press(self, event):
        element = self.notebook.identify(event.x, event.y)
        if element == "label":
            index = self.notebook.index(f"@{event.x},{event.y}")
            self.drag_data["tab_id"] = self.notebook.tabs()[index]
            self.drag_data["index"] = index

    def on_drag(self, event):
        if self.drag_data["tab_id"] is None:
            return

        try:
            # Find where we are dragging to
            dest_index = self.notebook.index(f"@{event.x},{event.y}")
            
            # If dest_index is different from current index, move it
            if dest_index != self.drag_data["index"]:
                # Perform visual move
                self.notebook.insert(dest_index, self.drag_data["tab_id"])
                
                # Notify application to sync data model
                if self.on_reorder_callback:
                    self.on_reorder_callback(self.drag_data["index"], dest_index)
                
                # Update current tracking index
                self.drag_data["index"] = dest_index
        except tk.TclError:
            # Ignore errors if mouse is outside tab area
            pass

class CustomTable(eg.Element):
    """Custom table with visible tree column for avatars."""
    def __init__(self, values, headings, col_widths=None, key="-TABLE-", **kw):
        super().__init__("CustomTable", "ttk.Treeview", key, True, **kw)
        self.values = values
        self.headings = headings
        self.col_widths = col_widths or []
        self.has_children = False
        self._tree = None

    @property
    def widget(self):
        # Return the treeview widget instead of the container frame
        # to ensure compatibility with code that expects a Treeview.
        return self._tree
    
    @widget.setter
    def widget(self, value):
        # TkEasyGUI sets self.widget during widget creation.
        # We store it but we prefer self._tree for functionality.
        self._widget_val = value

    def create(self, win: eg.Window, parent: tk.Widget) -> tk.Widget:
        # Create container frame to hold treeview and scrollbars
        container = ttk.Frame(parent, padding=0)
        
        # Inner frame to hold tree and vertical scrollbar above the horizontal scrollbar
        tree_frame = ttk.Frame(container)
        tree_frame.pack(side="top", fill="both", expand=True)
        
        columns = self.headings
        self._tree = ttk.Treeview(tree_frame, columns=columns, show="tree headings", selectmode="browse")
        
        # Scrollbars
        self.vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        self.hsb = ttk.Scrollbar(container, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=self.vsb.set, xscrollcommand=self.hsb.set)
        
        # Layout
        self._tree.pack(side="left", fill="both", expand=True, padx=0, pady=0)
        self.vsb.pack(side="right", fill="y", padx=0, pady=0)
        self.hsb.pack(side="bottom", fill="x", padx=0, pady=0)
        
        # Configure #0 column (tree column) for icons
        # Approximate size for 18px icon with no padding
        self._tree.column("#0", width=22, minwidth=22, stretch=False, anchor="center")
        self._tree.heading("#0", text="")

        # Force remove indentation/indicator space using direct Tcl call
        try:
            self._tree.tk.call(self._tree._w, "configure", "-indent", 0)
        except Exception:
            pass
        
        # Set headings
        for i, h in enumerate(self.headings):
            self._tree.heading(columns[i], text=h)
        
        # Set column widths
        if self.col_widths:
            for i, w in enumerate(self.col_widths):
                if i < len(columns):
                    # Set stretch=False to allow horizontal scrolling
                    self._tree.column(columns[i], width=w * 8, anchor="w", stretch=False)
        else:
            for i in range(len(columns)):
                self._tree.column(columns[i], width=100, anchor="w", stretch=False)
        
        # Insert values
        for row in self.values:
            # First item in row is treated as icon/image for #0 if it's an image, 
            # but usually we handle images separately. 
            # Prepend dummy for #0 if values match headings count.
            display_values = list(row)
            self._tree.insert("", "end", values=display_values)
        
        self.widget = self._tree
        return container

    def update(self, *args, **kwargs):
        if "values" in kwargs:
            self.values = kwargs["values"]
            if self._tree:
                # Clear existing
                for item in self._tree.get_children():
                    self._tree.delete(item)
                # Insert new
                for row in self.values:
                    self._tree.insert("", "end", values=row)
        super().update(*args, **kwargs)

class ColumnReorderer:
    """Helper class to enable reordering of columns in a ttk.Treeview."""
    def __init__(self, treeview_widget, on_change=None):
        self.treeview = treeview_widget
        self.on_change = on_change
        self.treeview.bind("<Button-1>", self.on_press, add="+")
        self.treeview.bind("<B1-Motion>", self.on_drag, add="+")
        self.drag_data = {"column": None, "x": None}

    def on_press(self, event):
        self.drag_data["column"] = None
        self.drag_data["x"] = None
        
        region = self.treeview.identify_region(event.x, event.y)
        if region != "heading":
            return
            
        col = self.treeview.identify_column(event.x)
        # Check if click is near the column boundary (resize hit area is ~8 pixels)
        col_left = self.treeview.identify_column(max(0, event.x - 8))
        col_right = self.treeview.identify_column(event.x + 8)
        
        if col_left != col or col_right != col:
            # Near separator, so user is likely resizing. Do not start reorder drag.
            return
            
        self.drag_data["column"] = col
        self.drag_data["x"] = event.x

    def on_drag(self, event):
        if self.drag_data["column"] is None or self.drag_data["column"] == "#0":
            return

        # Simple drag detection - if moved more than 10 pixels, consider it a drag
        if abs(event.x - self.drag_data["x"]) > 10:
            # Find target column
            target_column = self.treeview.identify_column(event.x)
            if target_column and target_column != self.drag_data["column"] and target_column != "#0":
                drag_col_id = self.treeview.column(self.drag_data["column"], "id")
                target_col_id = self.treeview.column(target_column, "id")

                # Get current display order
                current_order = list(self.treeview["displaycolumns"])
                if not current_order or current_order == ["#all"]:
                    # If no displaycolumns set, use all columns
                    current_order = list(self.treeview["columns"])
                
                # Find indices
                try:
                    from_idx = current_order.index(drag_col_id)
                    to_idx = current_order.index(target_col_id)
                    
                    # Swap
                    current_order[from_idx], current_order[to_idx] = current_order[to_idx], current_order[from_idx]
                    
                    # Apply new order
                    self.treeview["displaycolumns"] = current_order
                    if self.on_change:
                        self.on_change(self.treeview)

                    # Update drag ref to prevent flickering and allow continuous drag
                    self.drag_data["column"] = target_column
                    self.drag_data["x"] = event.x
                except ValueError:
                    pass  # Column not in order

    def get_display_order(self):
        """Return the current display order of columns."""
        order = self.treeview["displaycolumns"]
        if not order or list(order) == ["#all"]:
            order = self.treeview["columns"]
        return list(order)


class MainWindow:
    def __init__(self, login_callback, post_callback, refresh_callback, app_ref=None):
        self.login_callback = login_callback
        self.post_callback = post_callback
        self.refresh_callback = refresh_callback
        self.ui_settings = load_ui_state() or {}
        # Set default sash ratios if not present
        if "main_sash_ratio" not in self.ui_settings:
            self.ui_settings["main_sash_ratio"] = 0.55
        if "detail_sash_ratio" not in self.ui_settings:
            self.ui_settings["detail_sash_ratio"] = 0.7
        if "inline_post_sash_ratio" not in self.ui_settings:
            self.ui_settings["inline_post_sash_ratio"] = 0.65

        self.current_selected_post = None
        self.current_image_idx = 0
        self.inline_post_images = [] # List of {"data": data/path, "alt": ""}
        self.selected_image_index = 0 # Currently editing image index
        self._app_ref = app_ref
        self._avatar_queue = queue.Queue()
        self._avatar_workers_started = False
        self._prefetch_queue = queue.Queue()
        self._prefetch_workers_started = False
        self._image_cache = OrderedDict()  # url -> bytes (共有URLキャッシュ, LRU)
        self._image_cache_max = 500  # キャッシュ上限エントリ数
        self._detail_render_timer = None  # 長押し通過後に詳細ペインを本描画する after() のID
        self._space_held = False  # スペースが物理的に押されているか（KeyPress/KeyReleaseで更新）
        self._space_repeat_timer = None  # 長押し中の連続ジャンプ after() のID
        self._loaded_tabs = set() # tab_keys that have been refreshed at least once
        self._tree_items = {} # tab_key -> tuple(item_id): 直近描画時の行ID一覧（get_children 呼び出し削減用）
        
        # Load font settings
        self.font_family = self.ui_settings.get("font_family", "sans-serif")
        self.font_size = self.ui_settings.get("font_size", 10)

    def sync_columns(self, source_tree, tabs, window):
        try:
            order = list(source_tree["displaycolumns"])
            if not order or order == ['#all']:
                order = list(source_tree["columns"])
                
            widths = [source_tree.column("#0", "width")]
            for col in source_tree["columns"]:
                widths.append(source_tree.column(col, "width"))
                
            for tab in tabs:
                table_elem = window[f"-TIMELINE_{tab.tab_key}-"]
                if table_elem and table_elem.widget != source_tree:
                    tree = table_elem.widget
                    try:
                        tree["displaycolumns"] = order
                        tree.column("#0", width=widths[0])
                        for i, col in enumerate(tree["columns"]):
                            if (i + 1) < len(widths):
                                tree.column(col, width=widths[i + 1])
                    except Exception:
                        pass
        except Exception:
            pass
        
    def show_login(self, default_username=""):
        layout = [
            [eg.Text("Blueskyハンドル名（例：user.bsky.social）：")],
            [eg.Input(default_username, key="-HANDLE-")],
            [eg.Text("アプリパスワード：")],
            [eg.Input(key="-PASSWORD-", password_char="*")],
            [eg.Button("ログイン"), eg.Button("キャンセル")]
        ]
        window = eg.Window("Blueskyログイン", layout)

        while True:
            event, values = window.read()
            if event in (eg.WIN_CLOSED, "キャンセル"):
                window.close()
                return False, None, None

            if event == "ログイン":
                username = values["-HANDLE-"]
                password = values["-PASSWORD-"]

                if not username or not password:
                    import tkinter.messagebox as _mb
                    _mb.showerror("エラー", "ハンドル名とパスワードを入力してください。", parent=window.window)
                    continue

                success, error_msg = self.login_callback(username, password)
                if success:
                    window.close()
                    return True, username, password
                else:
                    import tkinter as tk
                    from tkinter import messagebox as _mb
                    _mb.showerror("ログインエラー", f"ログインに失敗しました：{error_msg}", parent=window.window)

    def _cache_get(self, url):
        """画像キャッシュ参照。ヒット時はLRU順を更新して bytes を返す。未ヒットは None。"""
        data = self._image_cache.get(url)
        if data is not None:
            self._image_cache.move_to_end(url)
        return data

    def _cache_put(self, url, data):
        """画像キャッシュ格納。上限超過時は最古(LRU)エントリを破棄し、常に直近分を保持する。"""
        self._image_cache[url] = data
        self._image_cache.move_to_end(url)
        while len(self._image_cache) > self._image_cache_max:
            self._image_cache.popitem(last=False)

    def _refresh_tree_items(self, window, tab_key):
        """タブの行ID一覧をキャッシュし直す。テーブル再描画（値差し替え）直後に呼ぶ。"""
        elem = window[f"-TIMELINE_{tab_key}-"]
        if elem and elem.widget:
            self._tree_items[tab_key] = elem.widget.get_children()

    def _get_tree_items(self, tree, tab_key):
        """キャッシュ済みの行ID一覧を返す。未キャッシュ時のみ get_children() で取得して格納する。"""
        items = self._tree_items.get(tab_key)
        if items is None:
            items = tree.get_children()
            self._tree_items[tab_key] = items
        return items

    def _display_image(self, window, data):
        """Helper to display image data smoothly on the -IMAGE- element."""
        if not data:
            return
        try:
            # Convert bytes to PIL Image
            img = PILImage.open(io.BytesIO(data))
            image_elem = window["-IMAGE-"]
            
            # Get actual widget size to fit the dynamic area
            # winfo_height() returns 1 if not yet displayed, so fallback to size or 400
            target_h = image_elem.widget.winfo_height()
            if target_h <= 1: target_h = 400
            target_w = image_elem.widget.winfo_width()
            if target_w <= 1: target_w = 400
            
            # Resize to fit the intended area while maintaining aspect ratio
            img.thumbnail((target_w, target_h), PILImage.Resampling.LANCZOS)
            # Set to widget with explicit runtime size
            image_elem.set_image(data=img, size=(target_w, target_h), resize_type=eg.ImageResizeType.FIT_BOTH)
            self.current_preview_image_data = data
            # Force refresh to minimize flicker/blank duration
            window.window.update()
        except Exception:
            pass

    def _update_detail_view(self, window, tab, row_idx, tabs, fast_pass=False):
        """選択された投稿を既読にし、詳細ペインを更新する。
        fast_pass: 未読ジャンプ長押しで通過中。既読化と該当行のタグ張り替えだけ行い、
        詳細ペインの描画と画像は着地後にまとめて反映する。"""
        if row_idx >= len(tab.posts):
            return
        post = tab.posts[row_idx]
        # ttk は selection_set() でも <<TreeviewSelect>> を発火するため、スペースジャンプ1回につき
        # -TIMELINE_*- 経由の描画がもう一度走る。表示中の投稿と同じなら捨てる。
        # （更新時は TimelineTabModel.update_posts が Post を作り直すので、
        #   リフレッシュ後の再描画は妨げない）
        if post is self.current_selected_post:
            return
        self.current_selected_post = post
        post.is_read = True
        if hasattr(self, "_app_ref") and self._app_ref:
            self._app_ref.read_post_uris.add(post.uri)
        self.current_image_idx = 0

        if fast_pass:
            # 既読になった1行ぶんだけ張り替える。全行走査も詳細ペインの描画もここではやらない
            self._apply_unread_tag_for_row(window, tab, row_idx)
            self._schedule_detail_render(window)
            return

        self._cancel_detail_render(window)
        # 選択で既読になるのは表示中タブの1投稿のみ。全タブ再走査は不要なので対象タブだけ更新する。
        self.apply_unread_tags(window, tabs, target_tab=tab)
        self._render_detail_pane(window, post)
        self._load_detail_images(window, post)

    def _render_detail_pane(self, window, post):
        """詳細ペインの見出し・本文・リプライ元・URLタグを描き直す。"""
        # Update detail view elements
        window["-DETAIL_AUTHOR-"].update(post.author_display_name)
        window["-DETAIL_HANDLE-"].update(f"@{post.author_handle}")
        window["-DETAIL_DATE-"].update(post.created_at)
        window["-DETAIL_REPOST-"].update(f"🔁 {post.repost_count}")
        window["-DETAIL_LIKE-"].update(f"❤ {post.like_count}")
        
        # Show repost-by info if available
        if getattr(post, 'is_repost', False) and getattr(post, 'reposted_by_author', None):
            by_handle = getattr(post, 'reposted_by_handle', '') or ''
            window["-DETAIL_REPOST_BY-"].update(f"🔁 reposted by {post.reposted_by_author} (@{by_handle})")
        else:
            window["-DETAIL_REPOST_BY-"].update("")
        
        if post.reply_to:
            window["-DETAIL_REPLY-"].update(f"↩ @{post.reply_to}")
        else:
            window["-DETAIL_REPLY-"].update("")
            
        text_widget = window["-DETAIL_TEXT-"].widget
        text_widget.config(state="normal")
        text_widget.delete("1.0", "end")
        
        text_widget.insert(tk.END, post.text + "\n")
        
        if hasattr(post, 'reply_parent_text') and post.reply_parent_text:
            text_widget.insert(tk.END, "\n\n")
            
            bg_color = text_widget.cget("bg")
            fg_color = text_widget.cget("fg")
            reply_frame = tk.Frame(text_widget, highlightbackground="pink", highlightthickness=1, bg=bg_color)
            
            content = post.reply_parent_text or ""
            reply_txt = tk.Label(reply_frame, text=content, justify="left", anchor="nw", bg=bg_color, fg=fg_color, font=(self.font_family, self.font_size))
            reply_txt.pack(fill="x", expand=True, padx=5, pady=(5, 0))
            
            meta_text = f"- {post.reply_parent_author} (@{post.reply_parent_handle})"
            date_text = getattr(post, 'reply_parent_created_at', '')
            if date_text: meta_text += f" {date_text}"
            meta_label = tk.Label(reply_frame, text=meta_text, justify="left", bg=bg_color, fg="gray", font=(self.font_family, self.font_size - 1))
            meta_label.pack(anchor="w", padx=5, pady=(0, 5))
            
            text_widget.window_create(tk.END, window=reply_frame, padx=10, pady=5)
            
            if not hasattr(text_widget, "_embedded_frames"):
                text_widget._embedded_frames = []
                def _on_resize(e):
                    for f, lbl in text_widget._embedded_frames:
                        try:
                            if f.winfo_exists():
                                fw = e.width - 35
                                if fw > 0:
                                    lbl.config(wraplength=fw - 15)
                        except Exception:
                            pass
                text_widget.bind("<Configure>", _on_resize, add="+")
            
            text_widget._embedded_frames = [(f, l) for f, l in text_widget._embedded_frames if f.winfo_exists()]
            text_widget._embedded_frames.append((reply_frame, reply_txt))
            
            text_widget.after(10, lambda rt=reply_txt, tw=text_widget: rt.config(wraplength=max(100, tw.winfo_width() - 50)))
        
        import re
        url_pattern = re.compile(r'https?://[^\s]+')
        content = text_widget.get("1.0", "end-1c")
        for match in url_pattern.finditer(content):
            start_idx = f"1.0 + {match.start()} chars"
            end_idx = f"1.0 + {match.end()} chars"
            text_widget.tag_add("url", start_idx, end_idx)
            
        text_widget.config(state="disabled")


    def _load_detail_images(self, window, post):
        """詳細ペインのアバターとプレビュー画像を反映する。
        キャッシュ済みは即描画、未キャッシュのみスレッドでDLして完了イベントを投げる。"""
        # Handle author avatar
        avatar_elem = window["-AUTHOR_AVATAR-"]
        if post.avatar_url:
            avatar_elem.erase()
            cached = self._cache_get(post.avatar_url)
            if cached is not None:
                # キャッシュヒット：即座にイベントを発行
                window.events.put(("-AVATAR_DOWNLOAD_COMPLETE-", {"data": cached}))
            else:
                def download_avatar_thread(url, win):
                    try:
                        resp = requests.get(url, timeout=10)
                        if resp.status_code == 200:
                            self._cache_put(url, resp.content)
                            win.events.put(("-AVATAR_DOWNLOAD_COMPLETE-", {"data": resp.content}))
                    except Exception:
                        pass
                threading.Thread(target=download_avatar_thread, args=(post.avatar_url, window), daemon=True).start()
        else:
            avatar_elem.erase()

        # Handle image preview
        image_elem = window["-IMAGE-"]
        idx_elem = window["-IMAGE_INDEX-"]
        pane_elem = window["-DETAIL_PANED-"]

        if post.thumbnail_urls:
            # Update index display
            idx_text = f"1 / {len(post.thumbnail_urls)}" if len(post.thumbnail_urls) > 1 else ""
            idx_elem.update(idx_text)

            # Show image area if hidden
            pane_elem.set_pane2_visible(True)
            # Force layout update to get valid winfo_width/height
            window.window.update_idletasks()

            # Double check that the image widget itself is packed within the column
            if not image_elem.widget.winfo_ismapped():
                try:
                    image_elem.widget.pack(expand=True, fill="both")
                except Exception: pass

            thumb_url = post.thumbnail_urls[0]
            cached = self._cache_get(thumb_url)
            if cached is not None:
                # キャッシュヒット：即座に描画
                self._display_image(window, cached)
            else:
                # 新しい画像を読み込む間も古い画像を残すため、ここでは erase() しない
                def download_thumb_thread(url, win):
                    try:
                        resp = requests.get(url, timeout=10)
                        if resp.status_code == 200:
                            self._cache_put(url, resp.content)
                            win.events.put(("-THUMB_DOWNLOAD_COMPLETE-", {"data": resp.content}))
                            try: win.window.quit()
                            except Exception: pass
                    except Exception:
                        pass
                threading.Thread(target=download_thumb_thread, args=(thumb_url, window), daemon=True).start()
        else:
            idx_elem.update("")
            # Hide image area
            pane_elem.set_pane2_visible(False)
            image_elem.erase()

    def _schedule_detail_render(self, window):
        """長押し通過中は詳細ペインの描画を丸ごと後回しにする。ジャンプのたびに張り直し、
        キーが途切れて DETAIL_RENDER_SETTLE_MS 経過したら着地した投稿だけ描く。"""
        self._cancel_detail_render(window)
        self._detail_render_timer = window.window.after(
            DETAIL_RENDER_SETTLE_MS, lambda: self._render_landed_post(window)
        )

    def _cancel_detail_render(self, window):
        """保留中の遅延描画を取り消す。マウス選択など即時描画する経路の先頭で呼ぶ。"""
        if self._detail_render_timer:
            window.window.after_cancel(self._detail_render_timer)
            self._detail_render_timer = None

    def _render_landed_post(self, window):
        """通過が終わった時点の投稿を本描画する。通過中に省いた分をここでまとめて反映する。"""
        self._detail_render_timer = None
        post = self.current_selected_post
        self._render_detail_pane(window, post)
        self._load_detail_images(window, post)

    def _apply_unread_tag_for_row(self, window, tab, row_idx):
        """既読になった1行だけタグを張り替える。通過中に全行(1タブぶん)を走査しないための軽量版。
        タグの見た目定義は apply_unread_tags が設定済みのものをそのまま使う。"""
        elem = window[f"-TIMELINE_{tab.tab_key}-"]
        if not elem:
            return
        tree: ttk.Treeview = elem.widget
        items = self._get_tree_items(tree, tab.tab_key)
        if row_idx >= len(items):
            return
        post = tab.posts[row_idx]
        tags = ["read"]
        if getattr(post, 'is_repost', False):
            tags.append("repost")
        if getattr(post, 'is_follower', False):
            tags.append("follower")
        tree.item(items[row_idx], tags=tuple(tags))
        self._update_tab_unread_indicator(window, tab)

    def _is_space_input_blocked(self, window):
        """入力欄で文字を打っている最中ならジャンプを無視する。
        空欄なら（IMEが残した空白を消したうえで）ジャンプを通す。"""
        focused = window.window.focus_get()
        if not isinstance(focused, (tk.Entry, ttk.Entry, tk.Text)):
            return False
        if isinstance(focused, tk.Text):
            if focused.get("1.0", "end-1c").strip() != "":
                return True
            focused.delete("1.0", "end")
        else:
            if focused.get().strip() != "":
                return True
            focused.delete(0, "end")
        return False

    def _on_space_press(self, window, tabs):
        """スペース押下。単押しぶんを即座に送り、押し続けられたら連続送りに入る。
        オートリピートのKeyPressは押下状態で弾くので、送り速度はタイマーだけが決める。"""
        if self._space_held:
            return
        if self._is_space_input_blocked(window):
            return
        self._space_held = True
        # 単押しはここで完結するので詳細ペインも画像もその場で描く
        self._jump_to_next_unread(window, tabs, fast_pass=False)
        self._space_repeat_timer = window.window.after(
            SPACE_REPEAT_DELAY_MS, lambda: self._space_repeat_tick(window, tabs)
        )

    def _space_repeat_tick(self, window, tabs):
        """長押し中の1送り。通過中は既読化だけに絞り、押されている間だけ自分を張り直す。"""
        if not self._space_held:
            self._space_repeat_timer = None
            return
        self._jump_to_next_unread(window, tabs, fast_pass=True)
        self._space_repeat_timer = window.window.after(
            SPACE_REPEAT_INTERVAL_MS, lambda: self._space_repeat_tick(window, tabs)
        )

    def _on_space_release(self, window):
        """スペース解放。連続送りを即座に止め、通過中に省いた描画をここで反映する。"""
        self._space_held = False
        if self._space_repeat_timer:
            window.window.after_cancel(self._space_repeat_timer)
            self._space_repeat_timer = None
        # 保留中＝直前の送りが描画を省いている。settleを待たずここで描く
        if self._detail_render_timer:
            self._cancel_detail_render(window)
            self._render_landed_post(window)

    def _jump_to_next_unread(self, window, tabs, fast_pass):
        """現在タブを優先して最も古い未読へ移動する。未読が尽きたらホームタブ先頭へ戻る。"""
        tab_group_elem = window["-TABGROUP-"]
        if not tab_group_elem:
            return

        # Find which tab is visually selected
        current_tab_id = tab_group_elem.widget.select()
        current_tab_v_idx = tab_group_elem.widget.index(current_tab_id)

        # Notebook.tabs() matches tabs in visual order
        v_tabs = tab_group_elem.widget.tabs()
        target_tab = None
        target_unread_idx = -1

        # Search starting from current visual index
        for i in range(len(tabs)):
            v_idx = (current_tab_v_idx + i) % len(tabs)
            v_tab_id = v_tabs[v_idx]
            # Find matching model by looking at its Tab widget id
            matching_tab_model = None
            for t_model in tabs:
                tab_elem = window[f"-TAB_{t_model.tab_key}-"]
                if tab_elem and str(tab_elem.widget) == v_tab_id:
                    matching_tab_model = t_model
                    break

            if not matching_tab_model:
                continue

            # Find currently selected row if in this tab
            start_idx = 0
            table_elem = window[f"-TIMELINE_{matching_tab_model.tab_key}-"]
            if table_elem:
                tree: ttk.Treeview = table_elem.widget
                sel = tree.selection()
                if sel:
                    items = tree.get_children()
                    try:
                        start_idx = items.index(sel[0]) + 1
                    except ValueError:
                        pass

            # 表示は index 0 が最新なので、選択行より上（＝古い方から新しい方）へ遡る
            sel_idx = start_idx - 1 if start_idx > 0 else len(matching_tab_model.posts)
            found_in_tab = -1

            for p_idx in range(sel_idx - 1, -1, -1):
                if not matching_tab_model.posts[p_idx].is_read:
                    found_in_tab = p_idx
                    break

            # If not found, wrap around to the bottom
            if found_in_tab == -1:
                for p_idx in range(len(matching_tab_model.posts) - 1, sel_idx, -1):
                    if not matching_tab_model.posts[p_idx].is_read:
                        found_in_tab = p_idx
                        break

            if found_in_tab != -1:
                target_tab = matching_tab_model
                target_unread_idx = found_in_tab
                break

        # target_tab が None のまま（全タブ未読なし）の場合
        # 現在ホームタブの先頭ポストを見ていない場合はそこへジャンプ
        if not target_tab and tabs:
            home_tab = tabs[0]  # ホームタブ＝最初のタブ
            home_table_elem = window[f"-TIMELINE_{home_tab.tab_key}-"]
            if home_table_elem and home_tab.posts:
                home_tree: ttk.Treeview = home_table_elem.widget
                home_items = home_tree.get_children()
                if home_items:
                    # 現在選択中のビジュアルタブがホームかどうか
                    home_tab_elem = window[f"-TAB_{home_tab.tab_key}-"]
                    current_tab_widget = str(tab_group_elem.widget.select())
                    home_tab_widget = str(home_tab_elem.widget) if home_tab_elem else ""
                    is_on_home_tab = (current_tab_widget == home_tab_widget)

                    # ホームタブの選択行
                    currently_selected = home_tree.selection()
                    is_on_first_row = (currently_selected and currently_selected[0] == home_items[0])

                    # 別タブにいる OR ホームタブだが先頭が選択されていない → 飛ぶ
                    if not is_on_home_tab or not is_on_first_row:
                        target_tab = home_tab
                        target_unread_idx = 0
                        tab_group_elem.widget.select(home_tab_elem.widget)

        if not target_tab:
            return

        # Switch to this tab visually
        target_tab_elem = window[f"-TAB_{target_tab.tab_key}-"]
        tab_group_elem.widget.select(target_tab_elem.widget)

        # Select this row in the corresponding table
        table_elem = window[f"-TIMELINE_{target_tab.tab_key}-"]
        if table_elem:
            tree: ttk.Treeview = table_elem.widget
            items = tree.get_children()
            if target_unread_idx < len(items):
                item_id = items[target_unread_idx]
                tree.selection_set(item_id)
                tree.see(item_id)
                self._update_detail_view(window, target_tab, target_unread_idx, tabs, fast_pass=fast_pass)

    def format_post_for_list(self, post):
        # Return a row for the table: [Name, Reply To, Text, Date, RT, Like]
        reply_str = f"@{post.reply_to}" if post.reply_to else ""
        # Remove newlines for list view
        display_text = post.text.replace("\n", " ") if post.text else ""
        return [post.author_display_name, reply_str, display_text, post.created_at, post.repost_count, post.like_count]

    def apply_unread_tags(self, window: eg.Window, tabs, target_tab=None): # Changed type hint from List[TabModel] to list to avoid import issues
        # target_tab 指定時はそのタブだけ処理する（投稿クリック時など、1タブしか状態が変わらないケース向け）。
        # tabs は list でも単一 tab でも受け付ける。
        if target_tab is not None:
            tabs = [target_tab]
        elif not isinstance(tabs, (list, tuple)):
            tabs = [tabs]
        for tab in tabs:
            elem = window[f"-TIMELINE_{tab.tab_key}-"]
            if elem:
                # Get the underlying treeview widget
                tree: ttk.Treeview = elem.widget
                # item-id は一度だけ取得する（旧実装は投稿数ぶん get_children() を呼び O(n²) だった）
                items = tree.get_children()

                for idx, item_id in enumerate(items):
                    if idx >= len(tab.posts):
                        # 投稿に対応しない余剰行はタグをクリアするだけ
                        tree.item(item_id, tags=())
                        continue
                    post = tab.posts[idx]
                    tags = ["unread" if not post.is_read else "read"]
                    if hasattr(post, 'is_repost') and post.is_repost:
                        tags.append("repost")
                    if hasattr(post, 'is_follower') and post.is_follower:
                        tags.append("follower")
                    tree.item(item_id, tags=tuple(tags))

                # Configure tags (only need to do this once per widget, but here is fine)
                tree.tag_configure("unread", font=(self.font_family, self.font_size, "bold"))
                tree.tag_configure("read", font=(self.font_family, self.font_size, "normal"))
                tree.tag_configure("repost", foreground="forestgreen")
                tree.tag_configure("follower", foreground="#CC6600")

            self._update_tab_unread_indicator(window, tab)

    def _update_tab_unread_indicator(self, window, tab):
        """タブ見出しの未読マーク（● ）を現在の未読数に合わせる。"""
        unread_count = sum(1 for p in tab.posts if not p.is_read)
        tab_group_elem = window["-TABGROUP-"]
        if not tab_group_elem:
            return
        # Find visual index of this tab_key to update its title
        try:
            # Notebook.tabs() returns list of child widgets
            tab_widgets = tab_group_elem.widget.tabs()
            tab_elem = window[f"-TAB_{tab.tab_key}-"]
            if tab_elem:
                v_idx = tab_widgets.index(str(tab_elem.widget))
                indicator = "● " if unread_count > 0 else ""
                tab_group_elem.widget.tab(v_idx, text=f"{indicator}{tab.name}")
        except Exception:
            pass

    def on_tab_reordered(self, tabs, old_index, new_index):
        """Called when a tab is visually reordered."""
        if old_index == new_index:
            return
        item = tabs.pop(old_index)
        tabs.insert(new_index, item)
        # Note: In-place modification of the shared 'tabs' list works here
        # since BlueskyApp holds a reference to the same list.

    def show_main(self, tabs: list):
        tab_layouts = []
        headings = ["名前", "返信先", "本文", "日時", "RT", "いいね"]
        
        for i, tab in enumerate(tabs):
            tab_name = tab.name
            if any(not p.is_read for p in tab.posts):
                tab_name = "● " + tab_name
            
            list_items = [self.format_post_for_list(p) for p in tab.posts]
            if not list_items:
                list_items = [["（投稿なし）", "", "更新してください", "", 0, 0]]

            # Use default character-based widths for constructor
            current_col_widths = [13, 12, 40, 19, 6, 6]

            tab_layout = [
                [CustomTable(
                    values=list_items, 
                    headings=headings, 
                    col_widths=current_col_widths,
                    key=f"-TIMELINE_{tab.tab_key}-", 
                    expand_x=True, 
                    expand_y=True,
                    pad=(0, 0)
                )]
            ]
            tab_layouts.append(eg.Tab(tab_name, tab_layout, key=f"-TAB_{tab.tab_key}-", pad=(0, 0)))
        # Keep a record of all tabs that have widgets in this session (includes later-hidden ones)
        all_tabs_in_session = list(tabs)

        menu_def = [
            ["File", ["New Post", "Refresh", "---", "Exit"]],
            ["フィード", ["(読み込み中...)"]],  # Rebuilt dynamically after window creation
            ["Account", ["Change Account"]],
            ["Settings", ["Options"]]
        ]

        # メインレイアウトの定義
        layout = [
            [eg.Menu(menu_def, key="-MENU-")],
            [
                PanedColumn(
                    layout1=[[
                        eg.Column([
                            [eg.TabGroup(tab_layouts, key="-TABGROUP-", expand_x=True, expand_y=True)],
                            # プレビューパネルを上段に定義 (場所を取らないようにしつつ、placeで浮遊表示)
                            [eg.Column([
                                [eg.Frame(" プレビュー (編集中) ", [
                                    [eg.Image(data=None, key="-INLINE_POST_BIG_PREVIEW-", size=(250, 200), background_color="black")],
                                    [eg.Text("代替テキスト(ALT):", font=("sans-serif", 9))],
                                    [eg.Input("", key="-INLINE_POST_ALT_TEXT-", font=("sans-serif", 10), expand_x=True, enable_events=True)],
                                    [eg.HSeparator()],
                                    [eg.Column([[
                                        eg.Column([
                                            [eg.Image(size=(50, 50), key=f"-INLINE_POST_THUMB_{i}-", background_color="silver", enable_events=True)],
                                            [eg.Button("×", key=f"-INLINE_POST_REMOVE_{i}-", size=(3, 1), font=("sans-serif", 7))]
                                        ], key=f"-INLINE_POST_SLOT_{i}-", pad=(2, 2)) for i in range(4)
                                    ]], key="-INLINE_POST_THUMB_ROW-", expand_x=True)],
                                    [eg.Button("閉じる", key="-INLINE_POST_CLOSE_PREVIEW-", size=(10, 1))]
                                ], relief="raised", borderwidth=3, background_color="#f0f0f0")]
                            ], key="-INLINE_POST_OVERLAY-", visible=True)]
                        ], expand_x=True, expand_y=True)
                    ]],
                    layout2=[[
                        PanedColumn(
                            layout1=[[
                                # 下段左側: 詳細情報 & 新規入力 (縦並び)
                                PanedColumn(
                                    layout1=[[
                                        eg.Frame(" 投稿詳細 ", [[
                                            # 左側: アイコン(アバター)のみ
                                            eg.Column([
                                                [eg.Image(data=None, key="-AUTHOR_AVATAR-", size=(40, 40), background_color="white")]
                                            ], vertical_alignment="top", pad=(0, 0)),
                                            
                                            # 右側: 名前・メタ・情報の縦積み
                                            eg.Column([
                                                [
                                                    eg.Text("", key="-DETAIL_AUTHOR-", font=("sans-serif", 10, "bold")),
                                                    eg.Text("", key="-DETAIL_HANDLE-", font=("sans-serif", 9), text_color="gray"),
                                                    eg.Text("", key="-DETAIL_DATE-", font=("sans-serif", 9), text_color="gray"),
                                                    eg.Text("", key="-DETAIL_REPOST-", font=("sans-serif", 9), text_color="steelblue"),
                                                    eg.Text("", key="-DETAIL_LIKE-", font=("sans-serif", 9), text_color="crimson"),
                                                    eg.Text("", key="-DETAIL_REPLY-", font=("sans-serif", 9), text_color="blue"),
                                                    eg.Text("", key="-DETAIL_REPOST_BY-", font=("sans-serif", 9, "italic"), text_color="forestgreen"),
                                                ],
                                                [eg.Multiline("", size=(60, 5), key="-DETAIL_TEXT-", expand_x=True, expand_y=True, readonly=True)],
                                            ], expand_x=True, expand_y=True, pad=(2, 0))
                                        ]], expand_x=True, expand_y=True)
                                    ]],
                                    layout2=[[
                                        eg.Frame(" 新規ポスト ", [
                                            [
                                                eg.Multiline("", size=(60, 2), key="-INLINE_POST_TEXT-", expand_x=True, enable_events=True),
                                                eg.Column([
                                                    [eg.Button("📁", key="-INLINE_POST_ADD_IMAGE-", size=(4, 1))],
                                                    [eg.Button("ポスト", key="-INLINE_POST_BTN-", size=(8, 1))]
                                                ], vertical_alignment="top")
                                            ],
                                            [
                                                eg.Column([
                                                    [
                                                        eg.Text("0枚", key="-INLINE_POST_IMAGE_INFO-", font=("sans-serif", 9), text_color="gray"),
                                                        eg.Button("クリア", key="-INLINE_POST_CLEAR_IMAGE-", size=(6, 1))
                                                    ]
                                                ], text_align="right", expand_x=True),
                                                eg.Text("0/300", key="-INLINE_POST_CHAR_COUNT-", font=("sans-serif", 9), text_color="gray")
                                            ]
                                        ], expand_x=True, expand_y=False)
                                    ]],
                                    key="-LEFT_COLUMN_PANED-",
                                    orient="vertical",
                                    weight1=2,
                                    weight2=1,
                                    minsize1=150,
                                    minsize2=120,
                                    expand_x=True, expand_y=True
                                )
                            ]],
                            layout2=[[
                                # 下段右側: 詳細画像
                                eg.Frame(" 画像 ", [[
                                    eg.Column([
                                        [eg.Text("", key="-IMAGE_INDEX-", font=("sans-serif", 9), text_color="gray")],
                                        [eg.Image(key="-IMAGE-", size=(400, 400), background_color="black", expand_x=True, expand_y=True)]
                                    ], expand_x=True, expand_y=True)
                                ]], expand_x=True, expand_y=True)
                            ]],
                            key="-DETAIL_PANED-", # 互換性のため旧名を使用
                            orient="horizontal",
                            weight1=2,
                            weight2=1,
                            expand_x=True, expand_y=True
                        )
                    ]],
                    key="-MAIN_PANED-",
                    orient="vertical",
                    weight1=2,
                    weight2=1,
                    minsize1=200,
                    minsize2=300,
                    expand_x=True, expand_y=True
                )
            ],
            [
                eg.Text(" 準備完了", key="-STATUS_BAR-", expand_x=True, relief="sunken", anchor="w", font=("sans-serif", 9))
            ]
        ]
        
        window_size = self.ui_settings.get("window_size", (1100, 900))
        window = eg.Window(APP_TITLE, layout, resizable=True, size=window_size,
                           element_padding=(0, 0), enable_key_events=True)
        
        # Initial read to build widgets
        event, values = window.read(timeout=10)
        
        # タイトルバーにユーザー統計情報を追加する非同期処理
        def _update_title_with_stats():
            try:
                username = self._app_ref.api.username
                if username:
                    p = self._app_ref.handle_get_profile(username)
                    if p:
                        follows = getattr(p, "follows_count", 0)
                        followers = getattr(p, "followers_count", 0)
                        posts = getattr(p, "posts_count", 0)
                        # disp_name = getattr(p, "display_name", p.handle) or p.handle
                        new_title = f"{APP_TITLE} - @{p.handle} [ フォロー: {follows} | フォロワー: {followers} | ポスト: {posts} ]"
                        window.events.put(("-UPDATE_TITLE-", {"title": new_title}))
            except Exception:
                pass
        import threading
        threading.Thread(target=_update_title_with_stats, daemon=True).start()

        
        # プレビューパーツをレイアウトから除外
        # overlay のinner frame だけでなく、TkEasyGUI が作る親行フレームも pack_forget し、
        # 上ペイン内に空のスペースが残らないようにする。
        # 表示時は place() で浮かせるので、ここで pack_forget しても支障ない。
        try:
            overlay_widget = window["-INLINE_POST_OVERLAY-"].widget
            if overlay_widget:
                overlay_widget.pack_forget()
                # TkEasyGUI の行フレーム（直接の親）も非表示にする
                if overlay_widget.master:
                    overlay_widget.master.pack_forget()
        except Exception: pass
        
        # Hide initial logic
        window["-DETAIL_PANED-"].set_pane2_visible(False)
        _set_elem_visible(window["-INLINE_POST_CLEAR_IMAGE-"], False)
        for i in range(4):
            _set_elem_visible(window[f"-INLINE_POST_SLOT_{i}-"], False)
        
        # MAIN_PANED（縦3ペイン全体）が expand+fill で正しくパックされるよう強制設定
        # TkEasyGUI はカスタム Element の expand_y を行フレームに伝播しない場合がある
        try:
            mp_widget = window["-MAIN_PANED-"].widget
            mp_widget.pack_configure(expand=True, fill="both")
            # 行フレーム（ttk.Frame）も same に設定
            if mp_widget.master:
                mp_widget.master.pack_configure(expand=True, fill="both")
        except Exception:
            pass
        
        # 全 Panedwindow を再帰的に探索して expand+fill を強制適用（ウィンドウツリー走査）
        # NOTE: master の side は変更しない。TkEasyGUI の行フレームは side='left' で
        # レイアウトされているため、side を変えるとレイアウトが壊れる。
        def _force_expand_all_panedwindows(widget):
            try:
                for child in widget.winfo_children():
                    if isinstance(child, ttk.Panedwindow):
                        try:
                            child.pack_configure(expand=True, fill="both")
                            if child.master:
                                child.master.pack_configure(expand=True, fill="both")
                        except Exception:
                            pass
                    _force_expand_all_panedwindows(child)
            except Exception:
                pass
        _force_expand_all_panedwindows(window.window)

        # TabGroup が MAIN_PANED の第1ペイン内で縦方向に広がれるよう、
        # TabGroup から pane1 まで間にある全ての親フレームを expand+fill する。
        # TkEasyGUI は内部的な行フレームを fill='x' のみで pack するため、
        # ここで明示的に fill='both' + expand=True を上書きする必要がある。
        try:
            tg_widget = window["-TABGROUP-"].widget
            pane1 = window["-MAIN_PANED-"].frame1
            if tg_widget and pane1:
                w = tg_widget
                for _ in range(10):  # 最大10階層上まで確認
                    if w is None or w == pane1:
                        break
                    try:
                        w.pack_configure(expand=True, fill="both")
                    except Exception:
                        pass
                    w = w.master
        except Exception:
            pass
        
        # MAIN_PANED のサッシュ位置を適用するヘルパー
        # NOTE: MAIN_PANED 自体の side は変更しない（TkEasyGUI の行フレームとの整合性を保つ）
        def _apply_main_sash(win, fallback_px=400):
            """MAIN_PANED サッシュを確実に適用するヘルパー（最低 100px 保護付き）"""
            try:
                me = win["-MAIN_PANED-"]
                if not (me and me.widget):
                    return
                # expand+fill のみ適用（side は変更しない）
                me.widget.pack_configure(expand=True, fill="both")
                if me.widget.master:
                    me.widget.master.pack_configure(expand=True, fill="both")
                win.window.update()
                mh = me.widget.winfo_height()
                ratio = self.ui_settings.get("main_sash_ratio")
                if mh > 50:
                    if ratio:
                        pos = int(mh * ratio)
                    else:
                        pos = int(mh * 0.55)
                    # 最低 100px / 最大 (mh-100px) の保護
                    pos = max(100, min(pos, mh - 100))
                    me.widget.sashpos(0, pos)
                else:
                    me.widget.sashpos(0, fallback_px)
            except Exception:
                pass
        try:
            window.window.update()
            _apply_main_sash(window)
        except Exception:
            pass

        # 初期状態で非表示に設定されているタブを隠す（バックグラウンドには存在する）
        if hasattr(self, "_app_ref") and self._app_ref:
            hidden_keys = getattr(self._app_ref, "hidden_tabs", set())
            if hidden_keys:
                tab_group_elem = window["-TABGROUP-"]
                # リストをコピーしてループ（元のtabsリストから要素を削除するため）
                for t in list(tabs):
                    hide_val = t.feed_uri if getattr(t, "feed_uri", None) else t.tab_key
                    if hide_val in hidden_keys:
                        tab_elem = window[f"-TAB_{t.tab_key}-"]
                        if tab_group_elem and tab_elem:
                            try:
                                tab_group_elem.widget.forget(tab_elem.widget)
                            except Exception:
                                pass
                        # 自動更新・現在表示中のタブ一覧から除外する
                        if t in tabs:
                            tabs.remove(t)

        # Manually fix avatar alignment and padding
        try:
            avatar_lbl = window["-AUTHOR_AVATAR-"].widget
            # Pack anchor 'nw' (North-West) ensures it stays top-left in the row.
            avatar_lbl.pack_configure(anchor="nw", padx=(5, 5), pady=(5, 0))
            parent = avatar_lbl.master
            for _ in range(3):
                if parent:
                    parent.pack_configure(anchor="nw")
                    parent = parent.master
            
            text_col_frame = window["-DETAIL_AUTHOR-"].widget.master.master
            text_col_frame.pack_configure(padx=(0, 5))
        except Exception as e:
            pass; # print(f"Could not adjust detail padding: {e}")

        # Bind Ctrl+Enter for Inline Post
        def submit_inline(e):
            window.events.put(("-INLINE_POST_BTN-", {"-INLINE_POST_TEXT-": window["-INLINE_POST_TEXT-"].get(), "images": self.inline_post_images}))
            return "break"
        
        try:
            widget = window["-INLINE_POST_TEXT-"].widget
            widget.bind("<Control-Return>", submit_inline)
            
            # クリップボードからの貼り付け (Ctrl+V)
            def handle_paste(e):
                self._handle_clipboard_image(window, "-INLINE_POST_")
                # テキストの貼り付けはデフォルト処理に任せるため "break" は返さない
            widget.bind("<Control-v>", handle_paste)

            # 文字数カウンターのリアルタイム更新 (KeyRelease で直接バインド)
            def _update_char_count(e=None):
                try:
                    text_val = widget.get("1.0", "end-1c")
                    count = len(text_val)
                    counter_elem = window["-INLINE_POST_CHAR_COUNT-"]
                    if counter_elem:
                        if count > 300:
                            over = count - 300
                            counter_elem.update(f"{count}/300 (+{over})", text_color="red")
                        else:
                            counter_elem.update(f"{count}/300", text_color="gray")
                except Exception:
                    pass
            widget.bind("<KeyRelease>", _update_char_count)
            # Ctrl+V 後にも更新されるよう遅延実行もセット
            def handle_paste(e):
                self._handle_clipboard_image(window, "-INLINE_POST_")
                widget.after(50, _update_char_count)
            widget.bind("<Control-v>", handle_paste)
            
            # Drag & Drop の設定 (windnd)
            def on_drop(files):
                print(f"[DEBUG DND] files dropped: {files}", flush=True)
                image_files = []
                for f in files:
                    if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif')):
                        image_files.append(f)
                
                if image_files:
                    self._add_images_to_list(window, "-INLINE_POST_", image_files)

            windnd.hook_dropfiles(window.window, on_drop, force_unicode=True)
            # 入力欄にも直接フック (重要: 他のペインに遮られないようにするため)
            windnd.hook_dropfiles(window["-INLINE_POST_TEXT-"].widget, on_drop, force_unicode=True)
            
        except Exception as e:
            pass

        # Force detail text multiline to stretch vertically (make its size variable/dynamic)
        try:
            txt_widget = window["-DETAIL_TEXT-"].widget
            txt_widget.pack(expand=True, fill="both")
            # The row frame containing the text widget
            if txt_widget.master:
                txt_widget.master.pack(expand=True, fill="both")
            # The Column frame containing the rows
            if txt_widget.master and txt_widget.master.master:
                txt_widget.master.master.pack(expand=True, fill="both")
        except Exception as e:
            pass; # print(f"Could not adjust text stretch: {e}")

        # Style for bottom tabs
        try:
            style = ttk.Style(window.window)
            style.configure("Bottom.TNotebook", tabposition="sw") # sw = South West (Bottom)
            style.configure("Bottom.TNotebook.Tab", padding=[5, 2])  # 左右5px、上下2pxの控えめな余白
            
            # 非選択時のタブを暗くする設定
            style.map("Bottom.TNotebook.Tab",
                foreground=[("selected", "black"), ("!selected", "#777777")],
                background=[("selected", "SystemButtonFace"), ("!selected", "#e0e0e0")]
            )
            
            style.configure("Treeview", rowheight=18, padding=0)

            
            # Redefine Treeview layout to remove indicators and indentation
            # This effectively removes the reserved space for +/- signs.
            # Treeitem.indicator is the element responsible for the indentation space.
            style.layout("Treeview.Item", 
                [('Treeitem.padding', {'sticky': 'nswe', 'children': [
                    ('Treeitem.image', {'side': 'left', 'sticky': 'nswe'}),
                    # ('Treeitem.indicator', {'side': 'left', 'sticky': ''}), # Removed
                    ('Treeitem.text', {'side': 'left', 'sticky': 'nswe'})
                ]})])

            window["-TABGROUP-"].widget.configure(style="Bottom.TNotebook")
        except Exception as e:
            pass; # print(f"Error applying tab style: {e}")

        # Connect reorderer
        reorderer = TabReorderer(window["-TABGROUP-"].widget, 
                                lambda old, new: self.on_tab_reordered(tabs, old, new))

        self.apply_unread_tags(window, tabs)
        window["-IMAGE-"].widget.pack_forget()

        # Replace the static TkEasyGUI menubar with a fully dynamic one
        self._build_menubar(window, tabs, all_tabs_in_session)
        
        # Apply current font to all elements
        self._update_ui_fonts(window)
        
        # FINAL HARD FIX for Treeview (Ensure icons visible, no tree-padding)
        for tab in tabs:
            elem = window[f"-TIMELINE_{tab.tab_key}-"]
            if elem and hasattr(elem, "_tree"):
                tree = elem._tree
                try:
                    tree.configure(show="tree headings") # RESTORE TREE
                    tree.column("#0", width=22, minwidth=22, stretch=False, anchor="center")
                    tree.tk.call(tree._w, "configure", "-indent", 0)
                    # Force container padding to 0
                    container = tree.master
                    container.pack_configure(padx=0, pady=0)
                    # Also check Tab content
                    tab_frame = container.master
                    if tab_frame:
                        tab_frame.pack_configure(padx=0, pady=0)
                except Exception: pass
        
        # Bind image configure for auto-resize
        def _on_image_configure(e):
            if hasattr(self, '_resize_timer') and self._resize_timer:
                window.window.after_cancel(self._resize_timer)
            self._resize_timer = window.window.after(300, lambda: window.dispatch_event("-REDRAW_PREVIEW_IMAGE-", {}))
        
        window["-IMAGE-"].widget.bind("<Configure>", _on_image_configure)

        # 未読ジャンプはイベントキュー経由(-WINDOW_KEY_EVENT-)ではなく押下状態を直接見る。
        # read() が1イベントごとに update()+mainloop() を回すため、キュー経由だとオートリピートが
        # 溜まって送りが遅く、キーを離した後も積み残しが処理されて行き過ぎていた。
        def _on_space_key_press(e):
            if e.keysym in SPACE_KEYSYMS or e.char in (" ", "　"):
                self._on_space_press(window, tabs)

        def _on_space_key_release(e):
            if e.keysym in SPACE_KEYSYMS or e.char in (" ", "　"):
                self._on_space_release(window)

        window.window.bind("<KeyPress>", _on_space_key_press, add="+")
        window.window.bind("<KeyRelease>", _on_space_key_release, add="+")

        # Bind double-click, right-click and column-reorder events
        self._column_reorderers = {}  # tab_key -> ColumnReorderer
        for tab in tabs:
            table_elem = window[f"-TIMELINE_{tab.tab_key}-"]
            if table_elem:
                table_elem.widget.bind("<Double-Button-1>", lambda e: window.dispatch_event("-TABLE_DOUBLE_CLICK-"))
                
                # Column drag reorder
                cr = ColumnReorderer(table_elem.widget, on_change=lambda t, tb=tabs, w=window: self.sync_columns(t, tb, w))
                self._column_reorderers[tab.tab_key] = cr

                def make_release_handler(t=table_elem.widget, tb=tabs, w=window):
                    def handler(e):
                        region = t.identify_region(e.x, e.y)
                        if region == "separator" or region == "heading":
                            self.sync_columns(t, tb, w)
                    return handler
                table_elem.widget.bind("<ButtonRelease-1>", make_release_handler())

                def make_rc_handler(t):
                    def on_right_click(e, _tab=t):
                        tree = window[f"-TIMELINE_{_tab.tab_key}-"].widget
                        item = tree.identify_row(e.y)
                        if item:
                            tree.selection_set(item)
                            items = tree.get_children()
                            try:
                                row_idx = list(items).index(item)
                                self._update_detail_view(window, _tab, row_idx, tabs)
                                self.show_post_context_menu(window, _tab, row_idx, e.x_root, e.y_root)
                            except ValueError:
                                pass
                    return on_right_click
                
                table_elem.widget.bind("<Button-3>", make_rc_handler(tab))

                def make_select_handler(tab_key=tab.tab_key):
                    def handler(e):
                        # スペース操作中の選択変更は _jump_to_next_unread が詳細ペインまで面倒を見る。
                        # ここでイベントを積むと read() の往復（update()+mainloop()）が
                        # 1ジャンプごとに丸ごと無駄になるので積まない。
                        if self._space_held:
                            return
                        window.dispatch_event(f"-TIMELINE_{tab_key}-")
                    return handler
                table_elem.widget.bind("<<TreeviewSelect>>", make_select_handler())
        
        image_elem = window["-IMAGE-"]
        if image_elem:
            image_elem.widget.bind("<Double-Button-1>", lambda e: window.dispatch_event("-IMAGE_DOUBLE_CLICK-"))
            # Cycle on scroll
            image_elem.widget.bind("<MouseWheel>", lambda e: window.dispatch_event("-IMAGE_CYCLE-", {"direction": -1 if e.delta > 0 else 1}))
            # Linux scroll
            image_elem.widget.bind("<Button-4>", lambda e: window.dispatch_event("-IMAGE_CYCLE-", {"direction": -1}))
            image_elem.widget.bind("<Button-5>", lambda e: window.dispatch_event("-IMAGE_CYCLE-", {"direction": 1}))

        for k in ["-DETAIL_AUTHOR-", "-DETAIL_HANDLE-", "-DETAIL_DATE-"]:
            elem = window[k]
            if elem and hasattr(elem, "widget"):
                elem.widget.bind("<Double-Button-1>", lambda e: window.dispatch_event("-TABLE_DOUBLE_CLICK-"))
                elem.widget.bind("<Button-3>", lambda e: self.show_detail_context_menu(window, e.x_root, e.y_root))

        text_widget = window["-DETAIL_TEXT-"].widget
        text_widget.tag_configure("url", foreground="blue", underline=True)
        
        def on_url_click(event):
            try:
                index = text_widget.index(f"@{event.x},{event.y}")
                tags = text_widget.tag_names(index)
                if "url" in tags:
                    ranges = text_widget.tag_ranges("url")
                    for i in range(0, len(ranges), 2):
                        if text_widget.compare(ranges[i], "<=", index) and text_widget.compare(index, "<=", ranges[i+1]):
                            url = text_widget.get(ranges[i], ranges[i+1])
                            import webbrowser
                            webbrowser.open(url)
                            break
            except Exception as e:
                pass; # print(f"URL click error: {e}")

        text_widget.tag_bind("url", "<Button-1>", on_url_click)
        text_widget.tag_bind("url", "<Enter>", lambda e: text_widget.config(cursor="hand2"))
        text_widget.tag_bind("url", "<Leave>", lambda e: text_widget.config(cursor=""))

        # Bind right-click on tabs
        tab_group = window["-TABGROUP-"]
        if tab_group:
            def on_right_click(e):
                pass; # print(f"Right click triggered at {e.x}, {e.y}")
                window.dispatch_event("-TAB_RIGHT_CLICK-", {"x": e.x, "y": e.y, "root_x": e.x_root, "root_y": e.y_root})
            tab_group.widget.bind("<Button-3>", on_right_click)

        # Apply complex UI state (sash, columns, window position) if saved
        if any(k in self.ui_settings for k in ["main_sash_pos", "detail_sash_pos", "right_sash_pos", "main_sash_ratio", "detail_sash_ratio", "right_sash_ratio", "inline_post_sash_ratio", "column_widths", "window_pos"]):
            def restore_complex_ui():
                try:
                    # Force geometry update first
                    window.window.update()

                    # 1. Restore window position
                    if "window_pos" in self.ui_settings:
                        wx, wy = self.ui_settings["window_pos"]
                        if -10000 < wx < 10000 and -10000 < wy < 10000:
                            window.window.geometry(f"+{wx}+{wy}")
                    
                    # 2. Outer Sash position (-MAIN_PANED-)
                    _apply_main_sash(window)
                    
                    # 3. Middle Sash position (Obsolete -RIGHT_PANE_SPLIT- removed)
                    
                    # 4. Inner Sash position (-DETAIL_PANED-)
                    try:
                        detail_elem = window["-DETAIL_PANED-"]
                        ratio = self.ui_settings.get("detail_sash_ratio")
                        saved_pos = self.ui_settings.get("detail_sash_pos")
                        detail_w = detail_elem.widget.winfo_width()
                        
                        # Pre-calculate intended sash position
                        target_sash = None
                        if ratio:
                            if detail_w > 50:
                                target_sash = int(detail_w * ratio)
                        elif saved_pos:
                            # Sanity check: saved_pos must be within widget width
                            if detail_w > 50 and saved_pos < detail_w:
                                target_sash = saved_pos
                            
                        if target_sash is not None:
                            # Save it internally in case it's currently hidden
                            detail_elem._saved_sash_pos = target_sash
                            # Apply if currently visible
                            if len(detail_elem.widget.panes()) > 1:
                                detail_elem.widget.sashpos(0, target_sash)
                    except Exception: 
                        pass
                    
                    # 5. Left Column Sash position (-LEFT_COLUMN_PANED-)
                    try:
                        inline_elem = window["-LEFT_COLUMN_PANED-"]
                        ratio = self.ui_settings.get("inline_post_sash_ratio")
                        if ratio:
                            try:
                                def apply_ratio():
                                    try:
                                        inline_elem = window["-LEFT_COLUMN_PANED-"]
                                        inline_h = inline_elem.widget.winfo_height()
                                        if inline_h > 50:
                                            inline_elem.widget.sashpos(0, int(inline_h * ratio))
                                            window.window.update()
                                    except Exception: pass
                                
                                apply_ratio()
                                # Apply again later to ensure it sticks after layout settles
                                window.window.after(500, apply_ratio)
                                window.window.after(1500, apply_ratio)
                            except Exception: pass
                        elif "inline_post_sash_pos" in self.ui_settings:
                            try:
                                inline_elem.widget.sashpos(0, self.ui_settings["inline_post_sash_pos"])
                                window.window.update()
                            except Exception: pass
                    except Exception:
                        pass

                    # Column widths
                    if "column_widths" in self.ui_settings:
                        widths = self.ui_settings["column_widths"]
                        for tab in tabs:
                            table_elem = window[f"-TIMELINE_{tab.tab_key}-"]
                            if table_elem:
                                tree = table_elem.widget
                                cols = tree["columns"]
                                if len(widths) == len(cols) + 1:
                                    tree.column("#0", width=widths[0])
                                    for j, w in enumerate(widths[1:]):
                                        tree.column(cols[j], width=w)
                                elif len(widths) == len(cols):
                                    for j, w in enumerate(widths):
                                        tree.column(cols[j], width=w)
                except Exception:
                    pass

            # Ensure overlay doesn't take space at the bottom initially
            try:
                ow = window["-INLINE_POST_OVERLAY-"].widget
                if ow:
                    ow.pack_forget()
                    if ow.master:
                        ow.master.pack_forget()
            except Exception: pass

            # Delay slightly to allow layout to settle
            # 複数タイミングでリトライし、確実にサッシュが適用されるようにする
            window.window.after(300, restore_complex_ui)
            window.window.after(900, lambda: _apply_main_sash(window))
            window.window.after(2000, lambda: _apply_main_sash(window))
        
        def _start_avatar_worker():
            def worker():
                while True:
                    task = self._avatar_queue.get()
                    if task is None: break
                    url, tab_key, row_idx, win = task
                    try:
                        data = self._cache_get(url)
                        if data is None:
                            # キャッシュミス：ダウンロードして格納
                            resp = requests.get(url, timeout=10)
                            if resp.status_code == 200:
                                data = resp.content
                                self._cache_put(url, data)
                            else:
                                data = None
                        if data:
                            win.events.put(("-LIST_AVATAR_DOWNLOAD_COMPLETE-", {"data": data, "tab_key": tab_key, "row_idx": row_idx}))
                    except Exception: pass
                    self._avatar_queue.task_done()
            for _ in range(8):  # 4 → 8 スレッドに増加
                threading.Thread(target=worker, daemon=True).start()
            self._avatar_workers_started = True

        def _start_prefetch_worker():
            def worker():
                while True:
                    url = self._prefetch_queue.get()
                    if url is None: break
                    try:
                        if self._cache_get(url) is None:
                            resp = requests.get(url, timeout=10)
                            if resp.status_code == 200:
                                self._cache_put(url, resp.content)
                    except Exception: pass
                    self._prefetch_queue.task_done()
            for _ in range(4):
                threading.Thread(target=worker, daemon=True).start()
            self._prefetch_workers_started = True

        def _queue_avatars(win, tab_models):
            if not self._avatar_workers_started:
                _start_avatar_worker()
            if not self._prefetch_workers_started:
                _start_prefetch_worker()
            
            if not hasattr(self, '_avatar_images'):
                self._avatar_images = {}
                
            for t_model in tab_models:
                for r_idx, p in enumerate(t_model.posts):
                    if p.avatar_url:
                        cached = self._cache_get(p.avatar_url)
                        if cached is not None:
                            # キャッシュヒット：ワーカー経由せず即イベント発行
                            win.events.put(("-LIST_AVATAR_DOWNLOAD_COMPLETE-", {
                                "data": cached,
                                "tab_key": t_model.tab_key,
                                "row_idx": r_idx
                            }))
                        else:
                            self._avatar_queue.put((p.avatar_url, t_model.tab_key, r_idx, win))
                    if getattr(p, "thumbnail_urls", None):
                        for thumb_url in p.thumbnail_urls:
                            if self._cache_get(thumb_url) is None:
                                self._prefetch_queue.put(thumb_url)
                        
        self._start_avatar_downloads = _queue_avatars

        def initial_refresh():
            window.events.put(("-ASYNC_REFRESH_START-", {}))
                
        window.window.after(100, initial_refresh)
        
        # Force initial save to ensure ui_config.json exists with defaults
        def initial_save():
            try:
                self.save_current_ui_state(window, tabs)
            except Exception:
                pass
        
        window.window.after(1000, initial_save)
        
        # Bind tab change for lazy loading
        def on_tab_changed(event):
            current_tab_id = event.widget.select()
            target_tab = None
            for t_model in tabs:
                tab_elem = window[f"-TAB_{t_model.tab_key}-"]
                if tab_elem and str(tab_elem.widget) == current_tab_id:
                    target_tab = t_model
                    break
            
            if target_tab and target_tab.tab_key not in self._loaded_tabs:
                window.events.put(("-ASYNC_REFRESH_START-", {"tab_key": target_tab.tab_key}))

        window["-TABGROUP-"].widget.bind("<<NotebookTabChanged>>", on_tab_changed)

        # Auto-refresh setup
        self._auto_refresh_timer = None
        def _do_auto_refresh():
            window.events.put(("-ASYNC_REFRESH_START-", {}))
            interval_min = self.ui_settings.get("auto_refresh", 0)
            if interval_min > 0:
                self._auto_refresh_timer = window.window.after(interval_min * 60 * 1000, _do_auto_refresh)

        interval_min = self.ui_settings.get("auto_refresh", 0)
        if interval_min > 0:
            self._auto_refresh_timer = window.window.after(interval_min * 60 * 1000, _do_auto_refresh)

        while True:
            event, values = window.read()
            if event in (None, "Exit"):
                self.save_current_ui_state(window, tabs)
                break
            if event == "-TAB_RIGHT_CLICK-":
                try:
                    import tkinter as tk
                    import os
                    data = values if isinstance(values, dict) else {}
                    x_rel = data.get("x", 0)
                    y_rel = data.get("y", 0)
                    x_root = data.get("root_x", 0)
                    y_root = data.get("root_y", 0)
                    
                    # Figure out which tab was clicked
                    tab_group = window["-TABGROUP-"]
                    nb = tab_group.widget
                    clicked_tab_idx_str = nb.tk.call(nb._w, "identify", "tab", x_rel, y_rel)
                    pass; # print(f"Right click coords: {x_rel}, {y_rel} -> identified tab index: {clicked_tab_idx_str}")
                    
                    if clicked_tab_idx_str != "" and clicked_tab_idx_str is not None:
                        clicked_tab_idx = int(clicked_tab_idx_str)
                        v_tabs = nb.tabs()
                        if clicked_tab_idx < len(v_tabs):
                            v_tab_id = v_tabs[clicked_tab_idx]
                            
                            # Find the matching model
                            target_tab = None
                            for t_model in tabs:
                                tab_elem = window[f"-TAB_{t_model.tab_key}-"]
                                if tab_elem and str(tab_elem.widget) == v_tab_id:
                                    target_tab = t_model
                                    break
                            
                            if target_tab:
                                # Build menu
                                menu = tk.Menu(window.window, tearoff=0)
                                
                                def set_sound(t, s):
                                    t.sound_file = s
                                    # Force save immediately
                                    self.save_current_ui_state(window, tabs)
                                
                                def hide_tab(t):
                                    if hasattr(self, "_app_ref") and self._app_ref:
                                        if not hasattr(self._app_ref, "hidden_tabs"):
                                            self._app_ref.hidden_tabs = set()
                                        hide_val = t.feed_uri if getattr(t, "feed_uri", None) else t.tab_key
                                        self._app_ref.hidden_tabs.add(hide_val)
                                        
                                        if t in tabs:
                                            tabs.remove(t)
                                        self.save_current_ui_state(window, tabs)
                                        
                                        tab_group_elem = window["-TABGROUP-"]
                                        tab_elem = window[f"-TAB_{t.tab_key}-"]
                                        if tab_group_elem and tab_elem:
                                            tab_group_elem.widget.forget(tab_elem.widget)
                                        self._refresh_feed_menu_entries(tabs, all_tabs_in_session, window)
                                
                                if target_tab.tab_type == "feed":
                                    menu.add_command(label="フィードを削除", command=lambda t=target_tab: hide_tab(t))
                                    menu.add_separator()
                                
                                menu.add_command(label="（サウンドなし）", command=lambda t=target_tab: set_sound(t, None))
                                menu.add_separator()
                                
                                sound_dir = get_resource_path("sound")
                                if not os.path.exists(sound_dir):
                                    os.makedirs(sound_dir, exist_ok=True)
                                
                                sounds = [f for f in os.listdir(sound_dir) if f.lower().endswith(('.wav', '.mp3'))]
                                if sounds:
                                    for s in sounds:
                                        # Show checkmark if current
                                        prefix = "✓ " if target_tab.sound_file == s else "  "
                                        menu.add_command(label=prefix + s, command=lambda t=target_tab, sf=s: set_sound(t, sf))
                                else:
                                    menu.add_command(label="sound/フォルダにサウンドファイルがありません", state="disabled")
                                
                                # Show popup
                                menu.tk_popup(x_root, y_root)
                except Exception as e:
                    pass; # print(f"Error handling tab right-click: {e}")
                    import traceback
                    pass
            elif event in (eg.WIN_CLOSED, "Exit"):
                self.save_current_ui_state(window, tabs)
                window.close()
                return "Exit"
            elif event == "-UPDATE_TITLE-":
                try:
                    data = values if isinstance(values, dict) else {}
                    new_title = data.get("title", "")
                    if new_title:
                        window.window.title(new_title)
                except Exception: pass
            elif event == "-UPDATE_AUTO_REFRESH-":
                interval = self.ui_settings.get("auto_refresh", 0)
                if hasattr(self, "_auto_refresh_timer") and self._auto_refresh_timer:
                    window.window.after_cancel(self._auto_refresh_timer)
                    self._auto_refresh_timer = None
                if interval > 0:
                    self._auto_refresh_timer = window.window.after(interval * 60 * 1000, _do_auto_refresh)
            elif isinstance(event, str) and event.startswith("-TIMELINE_"):
                try:
                    # Strip markers to find the base key
                    clean_event = event.strip("-")
                    tab = next((t for t in tabs if t.tab_key in clean_event), None)
                    if not tab:
                        pass; # print(f"No tab found for event: {event} (cleaned: {clean_event})")
                        continue
                    
                    table_elem = window[f"-TIMELINE_{tab.tab_key}-"]
                    if table_elem:
                        tree = table_elem.widget
                        sel = tree.selection()
                        if sel:
                            items = tree.get_children()
                            try:
                                row_idx = list(items).index(sel[0])
                                self._update_detail_view(window, tab, row_idx, tabs)
                            except ValueError:
                                pass
                except Exception as e:
                    pass; # print(f"Error handling timeline event {event}: {e}")
                    import traceback
                    pass
            elif event == "-THUMB_DOWNLOAD_COMPLETE-":
                data = values.get("data")
                if data:
                    self._display_image(window, data)
            elif event == "-REDRAW_PREVIEW_IMAGE-":
                if hasattr(self, 'current_preview_image_data') and self.current_preview_image_data:
                    try:
                        img = PILImage.open(io.BytesIO(self.current_preview_image_data))
                        image_elem = window["-IMAGE-"]
                        # Filter out invisible or uninitialized updates
                        if image_elem.widget.winfo_ismapped():
                            target_h = image_elem.widget.winfo_height()
                            target_w = image_elem.widget.winfo_width()
                            if target_h > 10 and target_w > 10:
                                img.thumbnail((target_w, target_h), PILImage.Resampling.LANCZOS)
                                image_elem.set_image(data=img, size=(target_w, target_h), resize_type=eg.ImageResizeType.FIT_BOTH)
                    except Exception as e:
                        pass; # print(f"Error redrawing image: {e}")
            elif event == "-AVATAR_DOWNLOAD_COMPLETE-":
                data = values.get("data")
                if data:
                    try:
                        # Convert bytes to PIL Image
                        img = PILImage.open(io.BytesIO(data))
                        avatar_elem = window["-AUTHOR_AVATAR-"]
                        # Resize to 40x40
                        img.thumbnail((40, 40), PILImage.Resampling.LANCZOS)
                        avatar_elem.set_image(data=img, size=(40, 40), resize_type=eg.ImageResizeType.FIT_BOTH)
                    except Exception as e:
                        pass; # print(f"Error displaying avatar: {e}")
            elif event == "-LIST_AVATAR_DOWNLOAD_COMPLETE-":
                data = values.get("data")
                tab_key = values.get("tab_key")
                row_idx = values.get("row_idx")
                if data and tab_key and row_idx is not None:
                    try:
                        # Convert bytes to PIL Image then to Tk PhotoImage
                        img = PILImage.open(io.BytesIO(data))
                        img.thumbnail((20, 20), PILImage.Resampling.LANCZOS)  # Small size for list
                        photo = PILImageTk.PhotoImage(img)
                        
                        # Store reference to prevent garbage collection
                        self._avatar_images[(tab_key, row_idx)] = photo
                        
                        # Set to treeview item
                        table_elem = window[f"-TIMELINE_{tab_key}-"]
                        if table_elem:
                            tree = table_elem.widget
                            items = self._get_tree_items(tree, tab_key)
                            if row_idx < len(items):
                                item_id = items[row_idx]
                                tree.item(item_id, image=photo)
                                pass; # print(f"Set avatar for {tab_key} row {row_idx}")
                    except Exception as e:
                        pass; # print(f"Error displaying list avatar: {e}")
            elif event == "-TABLE_DOUBLE_CLICK-":
                if self.current_selected_post:
                    url = self.get_post_web_url(self.current_selected_post)
                    if url:
                        webbrowser.open(url)
            elif event == "-IMAGE_DOUBLE_CLICK-":
                if self.current_selected_post and self.current_selected_post.full_image_urls:
                    idx = min(self.current_image_idx, len(self.current_selected_post.full_image_urls) - 1)
                    webbrowser.open(self.current_selected_post.full_image_urls[idx])
            elif event == "-IMAGE_CYCLE-":
                if self.current_selected_post and len(self.current_selected_post.thumbnail_urls) > 1:
                    data = values if isinstance(values, dict) else {}
                    direction = data.get("direction", 1)
                    n = len(self.current_selected_post.thumbnail_urls)
                    self.current_image_idx = (self.current_image_idx + direction) % n
                    new_idx = self.current_image_idx
                    idx_text = f"{new_idx + 1} / {n}"
                    window["-IMAGE_INDEX-"].update(idx_text)
                    url = self.current_selected_post.thumbnail_urls[new_idx]
                    cached = self._cache_get(url)
                    if cached is not None:
                        self._display_image(window, cached)
                    else:
                        def _dl_cycle(u=url, win=window):
                            try:
                                resp = requests.get(u, timeout=10)
                                if resp.status_code == 200:
                                    self._cache_put(u, resp.content)
                                    win.events.put(("-THUMB_DOWNLOAD_COMPLETE-", {"data": resp.content}))
                                    try: win.window.quit()
                                    except Exception: pass
                            except Exception:
                                pass
                        threading.Thread(target=_dl_cycle, daemon=True).start()
            elif event == "-WINDOW_KEY_EVENT-":
                # スペース（未読ジャンプ）は KeyPress/KeyRelease バインドで直接処理する
                if values.get("key") == "F5":
                    window.dispatch_event("Refresh")
                    continue
            elif event == "Refresh":
                window.events.put(("-ASYNC_REFRESH_START-", {}))
            elif event == "-ASYNC_REFRESH_START-":
                data = values if isinstance(values, dict) else {}
                target_tab_key = data.get("tab_key")
                
                # Update loading message for specific tab or all
                target_tabs = [t for t in tabs if t.tab_key == target_tab_key] if target_tab_key else tabs
                for tab in target_tabs:
                    table_elem = window[f"-TIMELINE_{tab.tab_key}-"]
                    if table_elem:
                        table_elem.update(values=[["（読み込み中…）", "", "投稿を取得しています…", "", 0, 0]])
                        # 行が差し替わったので行IDキャッシュも更新
                        self._refresh_tree_items(window, tab.tab_key)
                
                def _do_refresh(win, tk=target_tab_key):
                    try:
                        updated = self.refresh_callback(tab_key=tk)
                        win.events.put(("-ASYNC_REFRESH_COMPLETE-", {"tabs": updated, "tab_key": tk}))
                    except Exception as e:
                        win.events.put(("-ASYNC_REFRESH_COMPLETE-", {"error": str(e)}))
                
                threading.Thread(target=_do_refresh, args=(window,), daemon=True).start()
                
            elif event == "-ASYNC_REFRESH_COMPLETE-":
                data = values if isinstance(values, dict) else {}
                if "error" in data:
                    pass; # print("Refresh error:", data["error"])
                    continue
                updated_tabs = data.get("tabs", tabs)
                target_tab_key = data.get("tab_key")
                
                # Mark as loaded
                if target_tab_key:
                    self._loaded_tabs.add(target_tab_key)
                else:
                    for t in updated_tabs: self._loaded_tabs.add(t.tab_key)

                # Update only relevant tabs
                tabs_to_update = [t for t in updated_tabs if t.tab_key == target_tab_key] if target_tab_key else updated_tabs
                
                for tab_model in tabs_to_update:
                    list_items = [self.format_post_for_list(p) for p in tab_model.posts]
                    if not list_items:
                        list_items = [["(No posts yet)", "", "Click Refresh", "", 0, 0]]
                    
                    table_elem = window[f"-TIMELINE_{tab_model.tab_key}-"]
                    if table_elem:
                        table_elem.update(values=list_items)
                        # 新しい行IDをキャッシュ（アバター反映時の get_children 反復を回避）
                        self._refresh_tree_items(window, tab_model.tab_key)

                self.apply_unread_tags(window, updated_tabs)
                self._start_avatar_downloads(window, tabs_to_update)
            elif event == "-ACTION_REPOST-":
                post = values.get("post")
                if post and hasattr(self, "_app_ref") and self._app_ref:
                    threading.Thread(target=self._do_action_async, args=(window, "リポスト", self._app_ref.handle_repost, post), daemon=True).start()
            elif event == "-ACTION_LIKE-":
                post = values.get("post")
                if post and hasattr(self, "_app_ref") and self._app_ref:
                    threading.Thread(target=self._do_action_async, args=(window, "お気に入り", self._app_ref.handle_like, post), daemon=True).start()
            elif event == "-ACTION_FOLLOW-":
                author = values.get("author")
                if author and hasattr(self, "_app_ref") and self._app_ref:
                    threading.Thread(target=self._do_action_async, args=(window, "フォロー", self._app_ref.handle_follow, author), daemon=True).start()
            elif event == "-ACTION_UNFOLLOW-":
                author = values.get("author")
                if author and hasattr(self, "_app_ref") and self._app_ref:
                    threading.Thread(target=self._do_action_async, args=(window, "フォロー解除", self._app_ref.handle_unfollow, author), daemon=True).start()
            elif event == "-ACTION_SHOW_PROFILE-":
                author = values.get("author")
                if author and hasattr(self, "_app_ref") and self._app_ref:
                    threading.Thread(target=self._fetch_and_show_profile, args=(window, author), daemon=True).start()
            elif event == "-SHOW_PROFILE_DIALOG-":
                profile = values.get("profile")
                if profile:
                    self._show_profile_dialog(profile, window)
            elif event == "-ACTION_BOOKMARK-":
                post = values.get("post")
                if post and hasattr(self, "_app_ref") and self._app_ref:
                    threading.Thread(target=self._do_action_bookmark_async, args=(window, post), daemon=True).start()
            elif event == "-ACTION_COMPLETE-":
                data = values if isinstance(values, dict) else {}
                action = data.get("action")
                success = data.get("success")
                error = data.get("error")
                
                status_elem = window["-STATUS_BAR-"]
                if success:
                    if action == "ポスト":
                        try:
                            window["-INLINE_POST_TEXT-"].update("")
                            window["-INLINE_POST_CHAR_COUNT-"].update("0/300", text_color="gray")
                        except Exception: pass
                    if status_elem:
                        status_elem.update(f" 成功: {action}")
                        window.window.after(5000, lambda: status_elem.update(" 準備完了") if status_elem.widget.cget("text") == f" 成功: {action}" else None)
                else:
                    if status_elem:
                        status_elem.update(f" エラー ({action}): {error}")
                    import tkinter.messagebox as _mb
                    _mb.showerror("エラー", f"エラーが発生しました：\n{action}\n{error}", parent=window.window)
            elif isinstance(event, str) and event.startswith("-INLINE_POST_THUMB_"):
                try:
                    idx = int(event.split("_")[-1].strip("-"))
                    if idx < len(self.inline_post_images):
                        self.selected_image_index = idx
                        self._update_image_status(window, "-INLINE_POST_")
                except Exception: pass
            elif event == "-INLINE_POST_ALT_TEXT-":
                if 0 <= self.selected_image_index < len(self.inline_post_images):
                    new_alt = values.get("-INLINE_POST_ALT_TEXT-", "")
                    self.inline_post_images[self.selected_image_index]["alt"] = new_alt
            elif event == "-INLINE_POST_TEXT-":
                # 文字数カウンターを更新 (2バイト文字も1文字としてカウント)
                try:
                    text_val = values.get("-INLINE_POST_TEXT-", "") or ""
                    count = len(text_val)
                    MAX_CHARS = 300
                    counter_elem = window["-INLINE_POST_CHAR_COUNT-"]
                    if counter_elem:
                        if count > MAX_CHARS:
                            over = count - MAX_CHARS
                            counter_elem.update(f"{count}/300 (+{over})", text_color="red")
                        else:
                            counter_elem.update(f"{count}/300", text_color="gray")
                except Exception:
                    pass
            elif event == "-INLINE_POST_ADD_IMAGE-":
                files = filedialog.askopenfilenames(filetypes=[("Image files", "*.jpg *.jpeg *.png *.webp *.gif")])
                if files:
                    self._add_images_to_list(window, "-INLINE_POST_", list(files))
            elif event == "-INLINE_POST_CLEAR_IMAGE-":
                self.inline_post_images = []
                self.selected_image_index = 0
                self._update_image_status(window, "-INLINE_POST_")
            elif isinstance(event, str) and event.startswith("-INLINE_POST_REMOVE_"):
                try:
                    idx = int(event.split("_")[-1].strip("-"))
                    if idx < len(self.inline_post_images):
                        self.inline_post_images.pop(idx)
                        # Adjust selection index if needed
                        if self.selected_image_index >= len(self.inline_post_images):
                            self.selected_image_index = max(0, len(self.inline_post_images) - 1)
                        self._update_image_status(window, "-INLINE_POST_")
                except Exception:
                    pass
            elif event == "-INLINE_POST_BTN-":
                data_dict = values if isinstance(values, dict) else {}
                text = data_dict.get("-INLINE_POST_TEXT-", "")
                # Use current state of images which now includes alt text
                images = self.inline_post_images
                if text and text.strip():
                    if len(text.strip()) > 300:
                        import tkinter.messagebox as _mb
                        _mb.showwarning("文字数超過", f"300文字以内で入力してください。（現在 {len(text.strip())} 文字）", parent=window.window)
                    else:
                        self._do_action_async(window, "ポスト", self.post_callback, text.strip(), images)
            elif event == "New Post":
                self.show_post_dialog(window)
            elif event == "Change Account":
                self.save_current_ui_state(window, tabs)
                window.close()
                return "Logout"
            elif event == "Options":
                self.show_settings_dialog(window, tabs)

    def _build_menubar(self, window, tabs, all_tabs):
        """Build a full custom menubar with a dynamic feed visibility menu."""
        root = window.window

        # Destroy the existing menubar (created by eg.Menu)
        try:
            existing_name = root.cget('menu')
            if existing_name:
                root.nametowidget(existing_name).destroy()
        except Exception:
            pass

        menubar = tk.Menu(root)

        # ---- ファイル ----
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="新規ポスト", command=lambda: self.show_post_dialog(window))
        file_menu.add_command(label="更新",       command=lambda: window.dispatch_event("Refresh"))
        file_menu.add_separator()
        file_menu.add_command(label="終了",       command=lambda: window.dispatch_event("Exit"))
        menubar.add_cascade(label="ファイル", menu=file_menu)

        # ---- フィード (dynamic) ----
        self._feed_menu = tk.Menu(menubar, tearoff=0)
        self._refresh_feed_menu_entries(tabs, all_tabs, window)
        menubar.add_cascade(label="フィード", menu=self._feed_menu)

        # ---- アカウント ----
        account_menu = tk.Menu(menubar, tearoff=0)
        account_menu.add_command(label="アカウント切り替え", command=lambda: window.dispatch_event("Change Account"))
        menubar.add_cascade(label="アカウント", menu=account_menu)

        # ---- 設定 ----
        settings_menu = tk.Menu(menubar, tearoff=0)
        settings_menu.add_command(label="オプション", command=lambda: window.dispatch_event("Options"))
        menubar.add_cascade(label="設定", menu=settings_menu)

        root.config(menu=menubar)

    def _refresh_feed_menu_entries(self, tabs, all_tabs, window):
        """Rebuild entries in the フィード submenu, marking visible tabs with ✓."""
        if not hasattr(self, '_feed_menu') or self._feed_menu is None:
            return
        try:
            self._feed_menu.delete(0, 'end')
        except Exception:
            return

        visible_keys = {t.tab_key for t in tabs}
        for tab in all_tabs:
            is_visible = tab.tab_key in visible_keys
            label = f"\u2713 {tab.name}" if is_visible else f"  {tab.name}"
            self._feed_menu.add_command(
                label=label,
                command=lambda t=tab: self._toggle_tab_visibility(window, t, tabs, all_tabs)
            )

    def _toggle_tab_visibility(self, window, tab, tabs, all_tabs):
        """Toggle a tab between visible and hidden, updating the notebook and feed menu."""
        visible_keys = {t.tab_key for t in tabs}
        tab_group_elem = window["-TABGROUP-"]
        tab_elem = window[f"-TAB_{tab.tab_key}-"]

        if tab.tab_key in visible_keys:
            # --- Hide ---
            if tab_group_elem and tab_elem:
                try:
                    tab_group_elem.widget.forget(tab_elem.widget)
                except Exception:
                    pass
            if tab in tabs:
                tabs.remove(tab)
            if hasattr(self, "_app_ref") and self._app_ref:
                hide_val = getattr(tab, "feed_uri", None) or tab.tab_key
                if not hasattr(self._app_ref, "hidden_tabs"):
                    self._app_ref.hidden_tabs = set()
                self._app_ref.hidden_tabs.add(hide_val)
        else:
            # --- Show ---
            if tab_group_elem and tab_elem:
                try:
                    tab_group_elem.widget.add(tab_elem.widget, text=tab.name)
                except Exception:
                    pass
            if tab not in tabs:
                tabs.append(tab)
            if hasattr(self, "_app_ref") and self._app_ref:
                hide_val = getattr(tab, "feed_uri", None) or tab.tab_key
                if hasattr(self._app_ref, "hidden_tabs"):
                    self._app_ref.hidden_tabs.discard(hide_val)

        self.save_current_ui_state(window, tabs)
        self._refresh_feed_menu_entries(tabs, all_tabs, window)

    def _do_action_async(self, window, action_name, func, *args):
        try:
            success, err = func(*args)
            if success and action_name == "ポスト":
                window.window.after(100, lambda: self._reset_inline_post_images(window))
            window.events.put(("-ACTION_COMPLETE-", {"action": action_name, "success": success, "error": err}))
        except Exception as e:
            window.events.put(("-ACTION_COMPLETE-", {"action": action_name, "success": False, "error": str(e)}))

    def _reset_inline_post_images(self, window):
        self.inline_post_images = []
        self._update_image_status(window, "-INLINE_POST_")

    def _do_action_bookmark_async(self, window, post):
        try:
            success, msg = self._app_ref.handle_bookmark(post)
            # Re-apply unread tags/icons if possible, or just notification
            window.events.put(("-ACTION_COMPLETE-", {"action": msg, "success": success, "error": None}))
            # We don't necessarily need to refresh the whole tab, but if we are in bookmarked tab, we might want to.
            # For now, just save state.
            self.save_current_ui_state(window, []) # Tabs list can be empty for just saving other fields
        except Exception as e:
            window.events.put(("-ACTION_COMPLETE-", {"action": "ブックマーク", "success": False, "error": str(e)}))

    def show_post_context_menu(self, window, tab, row_idx, x_root, y_root):
        post = tab.posts[row_idx]
        self._show_context_menu(window, post, x_root, y_root)

    def show_detail_context_menu(self, window, x_root, y_root):
        if self.current_selected_post:
            self._show_context_menu(window, self.current_selected_post, x_root, y_root)

    def _show_context_menu(self, window, post, x_root, y_root):
        import tkinter as tk
        menu = tk.Menu(window.window, tearoff=0)
        menu.add_command(label="リポスト", command=lambda: window.dispatch_event("-ACTION_REPOST-", {"post": post}))
        menu.add_command(label="お気に入り", command=lambda: window.dispatch_event("-ACTION_LIKE-", {"post": post}))
        
        # Check if already bookmarked
        is_bookmarked = False
        if hasattr(self, "_app_ref") and self._app_ref:
            is_bookmarked = post.uri in getattr(self._app_ref, "bookmarked_uris", set())
        
        bm_label = "ブックマーク解除" if is_bookmarked else "ブックマーク"
        menu.add_command(label=bm_label, command=lambda: window.dispatch_event("-ACTION_BOOKMARK-", {"post": post}))

        menu.add_separator()
        menu.add_command(label="プロフィールを表示", command=lambda: window.dispatch_event("-ACTION_SHOW_PROFILE-", {"author": post.author_handle}))
        menu.add_command(label=f"@{post.author_handle} をフォロー", command=lambda: window.dispatch_event("-ACTION_FOLLOW-", {"author": post.author_handle}))
        menu.add_command(label=f"@{post.author_handle} のフォロー解除", command=lambda: window.dispatch_event("-ACTION_UNFOLLOW-", {"author": post.author_handle}))
        menu.tk_popup(x_root, y_root)

    def save_current_ui_state(self, window, tabs):
        try:
            state = {}
            # Window size - use winfo for live values
            win_w = window.window.winfo_width()
            win_h = window.window.winfo_height()
            if win_w > 100 and win_h > 100:
                state["window_size"] = (win_w, win_h)
            
            # Window position
            win_x = window.window.winfo_x()
            win_y = window.window.winfo_y()
            # Guard against extreme values during minimize/close (-32000 range)
            if -10000 < win_x < 10000 and -10000 < win_y < 10000:
                state["window_pos"] = (win_x, win_y)
            
            # Sash positions - ensure layout is current
            window.window.update()
            
            # 1. Main Divider (-MAIN_PANED-) - Vertical (Timeline T, Detail/Post B)
            try:
                main_elem = window["-MAIN_PANED-"]
                sp = main_elem.widget.sashpos(0)
                sh = main_elem.widget.winfo_height()
                if sh > 0: state["main_sash_ratio"] = sp / sh
                state["main_sash_pos"] = sp
            except Exception: pass
            
            # 2. Detail Divider (-DETAIL_PANED-) - Horizontal
            try:
                detail_elem = window["-DETAIL_PANED-"]
                if len(detail_elem.widget.panes()) > 1:
                    sp = detail_elem.widget.sashpos(0)
                    sw = detail_elem.widget.winfo_width()
                    if sw > 0: state["detail_sash_ratio"] = sp / sw
                    state["detail_sash_pos"] = sp
                else:
                    # Ensure we don't lose the sash if the pane is hidden
                    if hasattr(detail_elem, '_saved_sash_pos') and detail_elem._saved_sash_pos is not None:
                        state["detail_sash_pos"] = detail_elem._saved_sash_pos
                        if "detail_sash_ratio" in self.ui_settings:
                            state["detail_sash_ratio"] = self.ui_settings["detail_sash_ratio"]
                    elif "detail_sash_pos" in self.ui_settings:
                        state["detail_sash_pos"] = self.ui_settings["detail_sash_pos"]
                        if "detail_sash_ratio" in self.ui_settings:
                            state["detail_sash_ratio"] = self.ui_settings["detail_sash_ratio"]
            except Exception: pass

            # 3. Post Area Divider (Obsolete -RIGHT_PANE_SPLIT- removed)
            
            # 4. Left Column Divider (-LEFT_COLUMN_PANED-) - Vertical
            try:
                inline_elem = window["-LEFT_COLUMN_PANED-"]
                sp = inline_elem.widget.sashpos(0)
                sh = inline_elem.widget.winfo_height()
                if sh > 0: state["inline_post_sash_ratio"] = sp / sh
                state["inline_post_sash_pos"] = sp
            except Exception: pass
            
            # Column widths (from the first tab)
            if tabs:
                try:
                    tab_key = tabs[0].tab_key
                    table_key = f"-TIMELINE_{tab_key}-"
                    table_elem = window[table_key]
                    tree: ttk.Treeview = table_elem.widget
                    widths = [tree.column("#0", "width")]
                    # Only capture visible columns defined in the table
                    columns = tree["columns"]
                    for col in columns:
                        widths.append(tree.column(col, "width"))
                    state["column_widths"] = widths
                except Exception: pass
            
            # Column order (from the first tab's reorderer)
            if hasattr(self, '_column_reorderers') and self._column_reorderers and tabs:
                first_tab_key = tabs[0].tab_key
                if first_tab_key in self._column_reorderers:
                    state["column_order"] = self._column_reorderers[first_tab_key].get_display_order()
            
            # Tab Order
            state["tab_order"] = [t.tab_key for t in tabs]
            
            # Font Settings
            state["font_family"] = self.font_family
            state["font_size"] = self.font_size
            state["auto_refresh"] = self.ui_settings.get("auto_refresh", 0)
            
            # Read Posts and Hidden Tabs
            if hasattr(self, "_app_ref") and self._app_ref:
                state["read_post_uris"] = list(self._app_ref.read_post_uris)
                state["bookmarked_uris"] = list(getattr(self._app_ref, "bookmarked_uris", []))
                state["hidden_tabs"] = list(getattr(self._app_ref, "hidden_tabs", []))
            elif hasattr(self, "app_ref") and self.app_ref:
                state["read_post_uris"] = list(self.app_ref.read_post_uris)
                state["bookmarked_uris"] = list(getattr(self.app_ref, "bookmarked_uris", []))
                state["hidden_tabs"] = list(getattr(self.app_ref, "hidden_tabs", []))
            else:
                state["read_post_uris"] = []
                state["hidden_tabs"] = []

            # Save tab sounds
            tab_sounds = {}
            for t in tabs:
                if t.sound_file:
                    tab_sounds[t.tab_key] = t.sound_file
            state["tab_sounds"] = tab_sounds

            save_ui_state(state)
        except Exception:
            pass

    def get_post_web_url(self, post):
        if not post.uri:
            return None
        # at://did:plc:xxx/app.bsky.feed.post/yyy
        parts = post.uri.split("/")
        if len(parts) < 3:
            return None
        rkey = parts[-1]
        return f"https://bsky.app/profile/{post.author_handle}/post/{rkey}"

    def _handle_clipboard_image(self, window, prefix):
        """クリップボードから画像を取得して追加する"""
        try:
            im = ImageGrab.grabclipboard()
            if isinstance(im, PILImage.Image):
                # 一時ファイルとして保存するか、メモリ上のBytesIOとして保持する
                # ここでは内部リスト管理のために一時的なパスかBytesを扱う
                buf = io.BytesIO()
                # 元の形式が不明な場合はPNGで保存
                im.save(buf, format="PNG")
                img_bytes = buf.getvalue()
                
                # 特定の名前を付けてリストに追加
                temp_name = f"clipboard_{uuid.uuid4().hex[:8]}.png"
                self._add_images_to_list(window, prefix, [{"name": temp_name, "data": img_bytes}])
            elif isinstance(im, list):
                # クリップボードにファイルパスのリストがある場合
                image_files = [f for f in im if isinstance(f, str) and f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif'))]
                if image_files:
                    self._add_images_to_list(window, prefix, image_files)
        except Exception as e:
            pass

    def _add_images_to_list(self, window, prefix, new_images):
        """画像リストに新規画像を追加しUIを更新する (new_imagesはパスのリストまたは {name, data}のリスト)"""
        current_images = self.inline_post_images if prefix == "-INLINE_POST_" else getattr(self, "_dialog_images", [])
        
        print(f"[DEBUG DND] adding images to list: {new_images}", flush=True)
        for img in new_images:
            if len(current_images) >= 4:
                messagebox.showwarning("制限", "画像は最大4枚までです。")
                break
            
            # Wrap image in data object with alt text
            img_obj = {"data": img, "alt": ""}
            current_images.append(img_obj)
            
        if prefix == "-INLINE_POST_":
            self.inline_post_images = current_images
            # Default to selecting the newly added image if it's the first one or just added
            if len(current_images) > 0:
                self.selected_image_index = len(current_images) - 1
            self._update_image_status(window, "-INLINE_POST_")
        else:
            self._dialog_images = current_images
            # ダイアログ側の更新はダイアログ表示中のため別途考慮が必要
            if hasattr(self, "_active_dialog_window"):
                self._update_image_status(self._active_dialog_window, "-")

    def _update_image_status(self, window, key_prefix):
        """画像選択状態のUI表示を更新する"""
        is_inline = (key_prefix == "-INLINE_POST_")
        images = self.inline_post_images if is_inline else getattr(self, "_dialog_images", [])
        count = len(images)
        
        info_key = f"{key_prefix}IMAGE_INFO-"
        clear_key = f"{key_prefix}CLEAR_IMAGE-"
        
        # サムネイル表示の更新
        thumb_prefix = "INLINE_POST_" if is_inline else "DIAL_"
        thumb_row_key = f"-{thumb_prefix}THUMB_ROW-"
        for i in range(4):
            slot_key = f"-{thumb_prefix}SLOT_{i}-"
            thumb_key = f"-{thumb_prefix}THUMB_{i}-"
            
            if i < count:
                img_item = images[i]
                # Handle both dict and raw formats for safety
                img_raw = img_item["data"] if isinstance(img_item, dict) and "data" in img_item else img_item
                try:
                    # サムネイル生成 (60x60)
                    if isinstance(img_raw, str): # Path
                        pil_img = PILImage.open(img_raw)
                    else: # Clipboard dict {name, data}
                        pil_img = PILImage.open(io.BytesIO(img_raw["data"]))
                    
                    pil_img.thumbnail((60, 60), PILImage.Resampling.LANCZOS)
                    
                    # バイナリデータに変換して確実に更新
                    bio = io.BytesIO()
                    pil_img.save(bio, format="PNG")
                    data_bytes = bio.getvalue()
                    
                    # set_image() を使用して更新
                    try:
                        window[thumb_key].set_image(data=data_bytes)
                        
                        # ハイライト処理 (選択中の画像を目立たせる)
                        bg_color = "steelblue" if (is_inline and i == self.selected_image_index) else "silver"
                        window[thumb_key].widget.config(background=bg_color)
                        window[slot_key].widget.config(background=bg_color)
                        
                        _set_elem_visible(window[slot_key], True)
                    except Exception: pass
                except Exception:
                    _set_elem_visible(window[slot_key], False)
            else:
                _set_elem_visible(window[slot_key], False)
        
        # プレビューエリアとALT入力欄の更新 (インライン時のみ)
        if is_inline:
            overlay_key = "-INLINE_POST_OVERLAY-"
            big_preview_key = "-INLINE_POST_BIG_PREVIEW-"
            alt_text_key = "-INLINE_POST_ALT_TEXT-"
            
            overlay_elem = window[overlay_key]
            if count > 0:
                # Use place to overlay on top of the list area
                # Target: covering Right part of the main paned window (timeline)
                try:
                    # Request information about window size
                    win_w = window.window.winfo_width()
                    
                    # Position it over the timeline area
                    if win_w > 100:
                        # TabGroup自体をコンテナとして右上に配置する（Tkinterの制約によりroot等上位の祖先にはplaceできないため）
                        target_frame = window["-TABGROUP-"].widget
                        master_frame = overlay_elem.widget.master
                        
                        # Ensure the widget itself is packed within its master
                        overlay_elem.widget.pack(expand=True, fill="both")
                        
                        # Place the master frame to make the whole row float
                        master_frame.place(in_=target_frame, relx=0.98, rely=0.08, relwidth=0.45, anchor="ne")
                        master_frame.lift()
                        # Force map if hidden
                        master_frame.update_idletasks()
                except Exception: pass
                
                # 選択中の画像を大きく表示
                try:
                    current_idx = min(self.selected_image_index, count - 1)
                    self.selected_image_index = current_idx # Ensure valid index
                    
                    selected_img = images[current_idx]
                    img_raw = selected_img["data"]
                    
                    if isinstance(img_raw, str): pil_big = PILImage.open(img_raw)
                    else: pil_big = PILImage.open(io.BytesIO(img_raw["data"]))
                    
                    # 大きめのプレビューに縮小
                    pil_big.thumbnail((400, 250), PILImage.Resampling.LANCZOS)
                    bio = io.BytesIO()
                    pil_big.save(bio, format="PNG")
                    window[big_preview_key].set_image(data=bio.getvalue())
                    
                    # ALTテキストの値を同期
                    window[alt_text_key].update(selected_img.get("alt", ""))
                except Exception: pass
            else:
                # Hide overlay
                try:
                    overlay_elem.widget.place_forget()
                    if overlay_elem.widget.master:
                        overlay_elem.widget.master.place_forget()
                except Exception: pass
        
        # 行全体の表示・非表示を切り替え (余白をなくすため)
        try:
            _set_elem_visible(window[thumb_row_key], count > 0)
        except Exception: pass

        # 強制的に再描画
        window.window.update_idletasks()
        
        # エリアサイズの自動調節 (インライン投稿時)
        if is_inline:
            try:
                pane = window["-LEFT_COLUMN_PANED-"]
                h = pane.widget.winfo_height()
                if h > 150:
                    # Provide an ideal ratio, but guarantee at least 120px for the bottom 'New Post' area
                    # so that the multiline text input and buttons are never crushed out of existence.
                    target_ratio = 0.6 if count > 0 else 0.8
                    pos = int(h * target_ratio)
                    if (h - pos) < 120:
                        pos = h - 120
                    # Also guarantee top pane doesn't vanish entirely
                    if pos < 50: pos = 50
                    pane.widget.sashpos(0, pos)
            except Exception: pass
        
        # テキスト情報と一括クリアボタンの更新
        try:
            window[info_key].update(f"{count}枚")
            _set_elem_visible(window[clear_key], count > 0)
        except Exception: pass

    def show_post_dialog(self, parent_window):
        location = None
        try:
            location = (parent_window.window.winfo_x() + 50, parent_window.window.winfo_y() + 50)
        except Exception:
            pass
        
        self._dialog_images = []
        
        layout = [
            [eg.Text("今なに考えてる？")],
            [
                eg.Multiline(size=(40, 5), key="-TEXT-", expand_x=True, expand_y=True),
                eg.Column([
                    [eg.Button("📁 画像追加", key="-ADD_IMAGE-", size=(12, 1))],
                    [eg.Button("ポスト", key="ポスト", size=(12, 2))],
                    [eg.Button("キャンセル", size=(12, 1))]
                ])
            ],
            [
                # サムネイル表示エリア (最大4枚)
                eg.Column([[
                    eg.Column([
                        [eg.Image(size=(60, 60), key=f"-DIAL_THUMB_{i}-", background_color="silver")],
                        [eg.Button("×", key=f"-DIAL_REMOVE_{i}-", size=(4, 1), font=("sans-serif", 8))]
                    ], key=f"-DIAL_SLOT_{i}-", pad=(2, 2)) for i in range(4)
                ]], key="-DIAL_THUMB_ROW-", expand_x=True),
                eg.Column([
                    [eg.Text("0枚", key="-IMAGE_INFO-", font=("sans-serif", 9), text_color="gray")],
                    [eg.Button("クリア", key="-CLEAR_IMAGE-", size=(6, 1))]
                ], text_align="right")
            ]
        ]
        window = eg.Window("新規ポスト", layout, resizable=True, location=location)
        self._active_dialog_window = window
        
        # Hide initially
        window.read(timeout=1)
        _set_elem_visible(window["-CLEAR_IMAGE-"], False)
        for i in range(4):
            _set_elem_visible(window[f"-DIAL_SLOT_{i}-"], False)
        def submit_post(e):
            window.events.put(("ポスト", {"-TEXT-": window["-TEXT-"].get()}))
            return "break"
        
        # Bind Ctrl+V
        def handle_paste(e):
            self._handle_clipboard_image(window, "-")
            
        widget = window["-TEXT-"].widget
        widget.after(100, lambda: widget.bind("<Control-Return>", submit_post))
        widget.after(100, lambda: widget.bind("<Control-v>", handle_paste))
        
        # D&D for dialog
        def on_drop_dialog(files):
            image_files = []
            for f in files:
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif')):
                    image_files.append(f)
            if image_files:
                self._add_images_to_list(window, "-", image_files)
        
        windnd.hook_dropfiles(window.window, on_drop_dialog, force_unicode=True)

        while True:
            event, values = window.read()
            if event in (None, "キャンセル"):
                break
            
            if event == "-ADD_IMAGE-":
                files = filedialog.askopenfilenames(filetypes=[("Image files", "*.jpg *.jpeg *.png *.webp *.gif")])
                if files:
                    self._add_images_to_list(window, "-", list(files))
            
            if event == "-CLEAR_IMAGE-":
                self._dialog_images = []
                self._update_image_status(window, "-")
                
            if isinstance(event, str) and event.startswith("-DIAL_REMOVE_"):
                try:
                    idx = int(event.split("_")[-1].strip("-"))
                    if idx < len(self._dialog_images):
                        self._dialog_images.pop(idx)
                        self._update_image_status(window, "-")
                except Exception:
                    pass
                
            if event == "ポスト":
                data_dict = values if isinstance(values, dict) else {}
                text = data_dict.get("-TEXT-")
                if text and text.strip():
                    parent_window.events.put(("-INLINE_POST_BTN-", {
                        "-INLINE_POST_TEXT-": text.strip(),
                        "images": self._dialog_images
                    }))
                break
        
        window.close()
        self._active_dialog_window = None

    def show_settings_dialog(self, parent_window, tabs):
        import tkinter.font as tkfont
        
        location = None
        try:
            location = (parent_window.window.winfo_x(), parent_window.window.winfo_y())
        except Exception:
            pass
            
        # Get list of available fonts
        families = sorted([f for f in tkfont.families() if f and not f.startswith("@")])
        sizes = [8, 9, 10, 11, 12, 13, 14, 16, 18, 20]
        
        refresh_options = {"無効": 0, "1分": 1, "3分": 3, "5分": 5, "10分": 10, "15分": 15, "30分": 30}
        current_refresh_val = self.ui_settings.get("auto_refresh", 0)
        current_refresh_str = next((k for k, v in refresh_options.items() if v == current_refresh_val), "無効")
        
        layout = [
            [eg.Text("表示フォントの設定")],
            [eg.Text("フォント名:"), eg.Combo(families, default_value=self.font_family, key="-FONT_FAMILY-", expand_x=True)],
            [eg.Text("サイズ:"), eg.Combo(sizes, default_value=self.font_size, key="-FONT_SIZE-", readonly=True)],
            [eg.Text("タイムライン自動更新(分):"), eg.Combo(list(refresh_options.keys()), default_value=current_refresh_str, key="-AUTO_REFRESH-", readonly=True)],
            [eg.Button("保存", key="-SAVE_SETTINGS-"), eg.Button("キャンセル")]
        ]
        
        settings_win = eg.Window("設定", layout, modal=True, location=location)
        
        # TkEasyGUI は初回の read() 時に中央寄せなどの自動配置を行う場合があるため、
        # 一度非表示のまま（または一瞬だけ）読んでから位置を明示的に上書きします。
        settings_win.read(timeout=1)
        if location:
            settings_win.window.geometry(f"+{location[0]}+{location[1]}")
            
        event, values = settings_win.read()
        settings_win.close()
        
        if event == "-SAVE_SETTINGS-":
            if not values["-FONT_FAMILY-"] or not values["-FONT_SIZE-"]:
                return
            
            self.font_family = values["-FONT_FAMILY-"]
            try:
                self.font_size = int(values["-FONT_SIZE-"])
            except ValueError:
                self.font_size = 10
            
            if "-AUTO_REFRESH-" in values:
                self.ui_settings["auto_refresh"] = refresh_options[values["-AUTO_REFRESH-"]]
            
            # Save settings
            self.save_current_ui_state(parent_window, tabs)
            parent_window.events.put(("-UPDATE_AUTO_REFRESH-", {}))
            
            # Update entire UI font
            self.apply_unread_tags(parent_window, tabs)
            self._update_ui_fonts(parent_window)
            import tkinter.messagebox as _mb
            _mb.showinfo("設定", "設定を保存しました。", parent=parent_window.window)

    def _update_ui_fonts(self, window):
        # Update detail view fonts and other text/multiline elements
        detail_keys = [
            "-DETAIL_AUTHOR-", "-DETAIL_HANDLE-", "-DETAIL_DATE-", 
            "-DETAIL_REPOST-", "-DETAIL_LIKE-", "-DETAIL_REPLY-", "-DETAIL_REPOST_BY-"
        ]
        other_keys = ["-DETAIL_TEXT-", "-INLINE_POST_TEXT-", "-INLINE_POST_BTN-", "-IMAGE_INDEX-"]
        
        for k in detail_keys + other_keys:
            try:
                # Use try-except because "if k in window" might trigger integer indexing bug in some TkEasyGUI versions
                elem = window[k]
                if elem and hasattr(elem, "widget"):
                    if k in detail_keys:
                        # Extract existing style (bold/italic) to preserve it
                        curr = str(elem.widget.cget("font")).lower()
                        is_bold = "bold" in curr
                        is_italic = "italic" in curr or "oblique" in curr
                        
                        style_flags = []
                        if is_bold: style_flags.append("bold")
                        if is_italic: style_flags.append("italic")
                        
                        new_font = (self.font_family, self.font_size, " ".join(style_flags)) if style_flags else (self.font_family, self.font_size)
                        elem.update(font=new_font)
                    else:
                        # General elements
                        elem.update(font=(self.font_family, self.font_size))
            except Exception:
                # Key might not exist in this window instance
                continue

    def _fetch_and_show_profile(self, window, author_handle):
        try:
            profile = self._app_ref.handle_get_profile(author_handle)
            if profile:
                window.events.put(("-SHOW_PROFILE_DIALOG-", {"profile": profile}))
            else:
                window.events.put(("-ACTION_COMPLETE-", {"action": "プロフィール表示", "success": False, "error": "情報の取得に失敗しました"}))
        except Exception as e:
            window.events.put(("-ACTION_COMPLETE-", {"action": "プロフィール表示", "success": False, "error": str(e)}))

    def _show_profile_dialog(self, profile, parent_window):
        location = None
        try:
            location = (parent_window.window.winfo_x() + 50, parent_window.window.winfo_y() + 50)
        except Exception:
            pass
            
        desc = getattr(profile, "description", "") or ""
        followers = getattr(profile, "followers_count", 0)
        follows = getattr(profile, "follows_count", 0)
        posts = getattr(profile, "posts_count", 0)
        display_name = getattr(profile, "display_name", profile.handle)
        
        layout = [
            [eg.Text(f"{display_name}", font=(self.font_family, self.font_size + 4, "bold"))],
            [eg.Text(f"@{profile.handle}", font=(self.font_family, self.font_size), text_color="gray")],
            [eg.Text(f"フォロー: {follows}  フォロワー: {followers}  ポスト: {posts}", font=(self.font_family, self.font_size))],
            [eg.Frame("自己紹介", [[eg.Multiline(desc, size=(50, 8), readonly=True, expand_x=True, font=(self.font_family, self.font_size))]], expand_x=True)],
            [eg.Button("閉じる", key="Close")]
        ]
        win = eg.Window("プロフィール情報", layout, modal=True, location=location)
        win.read()
        win.close()

