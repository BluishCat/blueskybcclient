"""OS ごとの差分（キー・マウス操作、ドラッグ&ドロップ）を吸収するヘルパー。

主な対象は Windows。macOS / Linux はソースから実行した場合のベストエフォート対応で、
動作は未検証。
"""
import sys

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# 右クリック: macOS の Tk 8.6 は右ボタンを Button-2 として通知し（8.7 以降は Button-3）、
# Control+クリックも右クリック扱いが慣例
RIGHT_CLICK_EVENTS = ("<Button-3>", "<Button-2>", "<Control-Button-1>") if IS_MAC else ("<Button-3>",)

# ショートカットの修飾キー: macOS では Ctrl に加えて Cmd も受け付ける
_ACCEL_MODIFIERS = ("Control", "Command") if IS_MAC else ("Control",)


def bind_right_click(widget, func):
    for seq in RIGHT_CLICK_EVENTS:
        widget.bind(seq, func)


def bind_accel(widget, key, func):
    """Ctrl+<key>（macOS では Cmd+<key> も）に func をバインドする。key は 'v' や 'Return'"""
    for mod in _ACCEL_MODIFIERS:
        widget.bind(f"<{mod}-{key}>", func)


_dnd_ready = {}


def _ensure_tkdnd(widget):
    """tkdnd 拡張をウィジェットの Tcl インタプリタに読み込む。読み込めなければ False"""
    root = widget._root()
    key = str(root.tk.interpaddr())
    if key not in _dnd_ready:
        try:
            from tkinterdnd2 import TkinterDnD
            TkinterDnD.require(root)
            _dnd_ready[key] = True
        except Exception as e:
            print(f"[DnD] tkdnd を読み込めないためドラッグ&ドロップは無効です: {e}", flush=True)
            _dnd_ready[key] = False
    return _dnd_ready[key]


def hook_dropfiles(widget, func):
    """widget へのファイルドロップで func(paths: list[str]) を呼ぶ。登録できたら True"""
    if not _ensure_tkdnd(widget):
        return False
    from tkinterdnd2 import DND_FILES, COPY

    def on_drop(event):
        try:
            paths = [p for p in widget.tk.splitlist(event.data) if p]
            if paths:
                func(paths)
        except Exception as e:
            print(f"[DnD] drop handler error: {e}", flush=True)
        return COPY

    try:
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind("<<Drop>>", on_drop)
        return True
    except Exception as e:
        print(f"[DnD] drop target registration failed: {e}", flush=True)
        return False
