"""GitHub Releases を使った更新確認と本体（exe）の自己更新。

流れ:
  1. fetch_latest_release() で最新リリースを取得し、is_newer() で現在の版と比べる
  2. download_update() で zip を取得し、GitHub が公開している sha256 と照合してから
     中の exe を `<exe>.new` として展開する
  3. apply_update() で実行中の exe を `<exe>.old` にリネームし、`.new` を本来の名前に置く
     （Windows では実行中の exe は上書きできないが、同じボリューム内のリネームはできる）
  4. アプリ終了後に restart_app() で新しい exe を起動する
  5. 次回起動時に cleanup_leftovers() で `.old` などの残骸を消す

更新できるのは PyInstaller でビルドした Windows の exe だけ。ソースから実行しているときは
確認と通知のみ行う。
"""
import hashlib
import os
import subprocess
import sys
import zipfile

import requests

from utils.version import __version__

REPO = "BluishCat/poisora"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
EXE_NAME = "Poisora.exe"

_TIMEOUT = 15
_CHUNK = 256 * 1024


class UpdateError(Exception):
    """更新処理の失敗。メッセージはそのままユーザーに見せる。"""


def parse_version(text):
    """'v0.5.1' / '0.5.1' → (0, 5, 1)。数字以外を含む版（プレリリース等）は None。"""
    if not text:
        return None
    text = text.strip()
    if text[:1] in ("v", "V"):
        text = text[1:]
    parts = text.split(".")
    if not parts or not all(p.isdigit() for p in parts):
        return None
    nums = [int(p) for p in parts]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def is_newer(latest, current=__version__):
    lv, cv = parse_version(latest), parse_version(current)
    if lv is None or cv is None:
        return False
    return lv > cv


def can_self_update():
    """exe の置き換えまでできるか（PyInstaller でビルドした Windows 版のみ）。"""
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def _headers():
    return {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"Poisora/{__version__}",
    }


def fetch_latest_release(timeout=_TIMEOUT):
    """最新リリースの情報を dict で返す。取得できないときは UpdateError。"""
    try:
        resp = requests.get(LATEST_API, headers=_headers(), timeout=timeout)
    except requests.RequestException as e:
        raise UpdateError(f"GitHub に接続できませんでした: {e}") from e
    if resp.status_code == 404:
        raise UpdateError("公開されているリリースが見つかりませんでした。")
    if resp.status_code == 403:
        raise UpdateError("GitHub の API 制限に達しました。しばらくしてから再度お試しください。")
    if resp.status_code != 200:
        raise UpdateError(f"更新情報を取得できませんでした (HTTP {resp.status_code})。")
    try:
        data = resp.json()
    except ValueError as e:
        raise UpdateError("更新情報の形式が不正です。") from e

    tag = data.get("tag_name") or ""
    asset = None
    for a in data.get("assets") or []:
        name = a.get("name") or ""
        if name.lower().endswith(".zip") and name.startswith("Poisora-"):
            asset = a
            break

    digest = (asset or {}).get("digest") or ""
    sha256 = digest[len("sha256:"):].lower() if digest.startswith("sha256:") else None
    return {
        "tag": tag,
        "version": tag[1:] if tag[:1] in ("v", "V") else tag,
        "name": data.get("name") or tag,
        "body": data.get("body") or "",
        "html_url": data.get("html_url") or RELEASES_PAGE,
        "asset_name": (asset or {}).get("name"),
        "asset_url": (asset or {}).get("browser_download_url"),
        "asset_size": (asset or {}).get("size") or 0,
        "sha256": sha256,
    }


def _exe_path():
    return os.path.abspath(sys.executable)


def _staged_paths(exe=None):
    exe = exe or _exe_path()
    return {
        "part": exe + ".download",
        "new": exe + ".new",
        "old": exe + ".old",
    }


def _remove_quietly(path):
    try:
        if os.path.exists(path):
            os.remove(path)
        return True
    except OSError:
        return False


def download_update(info, on_progress=None, exe=None):
    """リリースの zip を取得して検証し、exe を `<exe>.new` に展開してそのパスを返す。

    on_progress(done_bytes, total_bytes) を時々呼ぶ（ワーカースレッドから）。
    """
    if not info.get("asset_url"):
        raise UpdateError("このリリースには更新用の zip が添付されていません。")
    if not info.get("sha256"):
        # 改ざん・破損を確認できないものは置き換えに使わない
        raise UpdateError("更新ファイルのハッシュ値が取得できないため、自動更新できません。\n"
                          "リリースページから手動で更新してください。")

    paths = _staged_paths(exe)
    _remove_quietly(paths["part"])
    _remove_quietly(paths["new"])

    total = int(info.get("asset_size") or 0)
    hasher = hashlib.sha256()
    done = 0
    try:
        with requests.get(info["asset_url"], headers={"User-Agent": f"Poisora/{__version__}"},
                          stream=True, timeout=_TIMEOUT) as resp:
            if resp.status_code != 200:
                raise UpdateError(f"更新ファイルをダウンロードできませんでした (HTTP {resp.status_code})。")
            total = int(resp.headers.get("Content-Length") or total or 0)
            with open(paths["part"], "wb") as f:
                for chunk in resp.iter_content(_CHUNK):
                    if not chunk:
                        continue
                    f.write(chunk)
                    hasher.update(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, total)
    except requests.RequestException as e:
        _remove_quietly(paths["part"])
        raise UpdateError(f"ダウンロード中にエラーが発生しました: {e}") from e
    except OSError as e:
        _remove_quietly(paths["part"])
        raise UpdateError(f"更新ファイルを保存できませんでした: {e}") from e

    if hasher.hexdigest().lower() != info["sha256"]:
        _remove_quietly(paths["part"])
        raise UpdateError("ダウンロードしたファイルのハッシュ値が一致しません（破損または改ざんの可能性）。")

    try:
        with zipfile.ZipFile(paths["part"]) as z:
            members = [n for n in z.namelist()
                       if n.replace("\\", "/").rsplit("/", 1)[-1].lower() == EXE_NAME.lower()]
            if len(members) != 1:
                raise UpdateError("更新用の zip に Poisora.exe が見つかりませんでした。")
            with z.open(members[0]) as src, open(paths["new"], "wb") as dst:
                while True:
                    buf = src.read(_CHUNK)
                    if not buf:
                        break
                    dst.write(buf)
    except (zipfile.BadZipFile, OSError) as e:
        _remove_quietly(paths["new"])
        raise UpdateError(f"更新ファイルを展開できませんでした: {e}") from e
    finally:
        _remove_quietly(paths["part"])

    with open(paths["new"], "rb") as f:
        if f.read(2) != b"MZ":
            _remove_quietly(paths["new"])
            raise UpdateError("展開した更新ファイルが実行ファイルではありません。")
    return paths["new"]


def apply_update(new_path, exe=None):
    """実行中の exe を `.old` に退避し、new_path を exe の位置に置く。失敗時は元に戻す。"""
    exe = exe or _exe_path()
    old = _staged_paths(exe)["old"]
    if os.path.exists(old) and not _remove_quietly(old):
        # 前回の .old がまだ使用中など。別名に逃がす
        old = f"{old}.{os.getpid()}"
    try:
        os.replace(exe, old)
    except OSError as e:
        raise UpdateError(f"現在の exe を置き換えられませんでした: {e}\n"
                          f"フォルダーへの書き込み権限を確認してください。") from e
    try:
        os.replace(new_path, exe)
    except OSError as e:
        try:
            os.replace(old, exe)
        except OSError:
            pass
        raise UpdateError(f"新しい exe を配置できませんでした: {e}") from e
    return exe


def restart_app(exe=None):
    """新しい exe を別プロセスで起動する（呼び出し側はこの後すぐ終了すること）。"""
    exe = exe or _exe_path()
    env = dict(os.environ)
    # PyInstaller onefile の子孫として起動すると親の展開先(_MEI…)を引き継いでしまうため、
    # 独立した新しいアプリとして起動させる (PyInstaller 6.9+)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([exe], cwd=os.path.dirname(exe), env=env,
                     creationflags=flags, close_fds=True)


def cleanup_leftovers(exe=None):
    """前回の更新で残った `.old` / `.new` / `.download` を消す（消せなければ次回に持ち越し）。"""
    if not can_self_update() and exe is None:
        return
    exe = exe or _exe_path()
    folder = os.path.dirname(exe)
    base = os.path.basename(exe)
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        if name.startswith(base + ".old") or name in (base + ".new", base + ".download"):
            _remove_quietly(os.path.join(folder, name))
