"""mp4 の表示サイズと再生時間を読むための最小限のパーサ。

app.bsky.embed.video の aspectRatio と添付前の長さチェックのためだけに使う。
ffprobe のような外部依存を持ち込みたくないので、必要な box だけを自前で辿る。
"""

import struct


def _read_moov(f):
    """先頭から box を辿り、moov の中身だけをメモリに読み込む

    動画は最大 300MB になるため、ファイル全体は読まずに seek で読み飛ばす。
    """
    while True:
        header = f.read(8)
        if len(header) < 8:
            return None

        size, box_type = struct.unpack(">I4s", header)
        header_size = 8
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]
            header_size = 16
        elif size == 0:
            # サイズ 0 は「ファイル末尾まで」を意味する
            return f.read() if box_type == b"moov" else None

        if size < header_size:
            return None
        if box_type == b"moov":
            return f.read(size - header_size)

        f.seek(size - header_size, 1)


def _iter_boxes(data, start, end):
    """[start, end) を box 単位で走査し (種別, 中身の開始, 中身の終端) を返す"""
    offset = start
    while offset + 8 <= end:
        size, box_type = struct.unpack_from(">I4s", data, offset)
        header_size = 8
        if size == 1:
            size = struct.unpack_from(">Q", data, offset + 8)[0]
            header_size = 16
        elif size == 0:
            size = end - offset

        if size < header_size or offset + size > end:
            return

        yield box_type, offset + header_size, offset + size
        offset += size


def _find_box(data, start, end, box_type):
    for btype, body_start, body_end in _iter_boxes(data, start, end):
        if btype == box_type:
            return body_start, body_end
    return None


def _read_duration_sec(data, start):
    """mvhd から再生時間(秒)を得る"""
    version = data[start]
    offset = start + 4  # version + flags
    if version == 1:
        offset += 16  # creation + modification
        timescale = struct.unpack_from(">I", data, offset)[0]
        duration = struct.unpack_from(">Q", data, offset + 4)[0]
    else:
        offset += 8
        timescale, duration = struct.unpack_from(">II", data, offset)

    # 断片化 mp4 (mvex を持つもの) は moov の duration が 0 になり、長さが分からない
    if not timescale or not duration:
        return None
    return duration / timescale


def _read_display_size(data, start):
    """tkhd から表示サイズを得る。回転を伴うトラックは縦横を入れ替える"""
    version = data[start]
    offset = start + 4  # version + flags
    offset += 32 if version == 1 else 20  # creation 〜 duration
    offset += 16  # reserved, layer, alternate_group, volume, reserved

    matrix = struct.unpack_from(">9i", data, offset)
    raw_width, raw_height = struct.unpack_from(">II", data, offset + 36)

    # 16.16 固定小数
    width = int(round(raw_width / 65536.0))
    height = int(round(raw_height / 65536.0))
    if not width or not height:
        return None

    # matrix の a,d が 0 で b,c が非ゼロなら 90/270 度回転
    a, b, c, d = matrix[0], matrix[1], matrix[3], matrix[4]
    if a == 0 and d == 0 and (b or c):
        width, height = height, width

    return width, height


def read_video_info(path):
    """mp4 から {"width", "height", "duration_sec"} を得る。読めなければ None

    ユーザーが選んだ任意のファイルが相手なので、想定外の構造では例外を投げずに None を返す。
    aspectRatio は lexicon 上 optional なため、取れなければ省略して投稿すればよい。
    """
    try:
        with open(path, "rb") as f:
            moov = _read_moov(f)
        if not moov:
            return None

        duration_sec = None
        mvhd = _find_box(moov, 0, len(moov), b"mvhd")
        if mvhd:
            duration_sec = _read_duration_sec(moov, mvhd[0])

        # 表示サイズを持つのは映像トラックだけなので、最初に取れたものを採用する
        for box_type, body_start, body_end in _iter_boxes(moov, 0, len(moov)):
            if box_type != b"trak":
                continue
            tkhd = _find_box(moov, body_start, body_end, b"tkhd")
            if not tkhd:
                continue
            size = _read_display_size(moov, tkhd[0])
            if size:
                return {"width": size[0], "height": size[1], "duration_sec": duration_sec}

        return None
    except Exception:
        return None
