"""文件名 → 歌名 / 艺人的启发式识别。

**这块很容易想当然，所以把规则写清楚**（都踩过）：

* ``标题 - 艺人`` 还是 ``艺人 - 标题``？—— 音乐文件下载的通行约定是
  **``艺人 - 歌名``**（"周杰伦 - 晴天.mp3"），但很多歌词站给的是反的。
  所以按线索判：含 ``(Live)`` / ``feat.`` / ``现场`` 的一侧是**歌名**；
  含多个名字分隔符（``&``、``、``、``/``、``_``、``,``）的一侧是**歌手**。
* 两边都没有线索时是**本质歧义**（"于文文 - 体面"和"体面 - 于文文"长得一样），
  这时按通行约定取 **``艺人 - 歌名``**。
* Web 版踩过的坑：以前"只在字段为空时才填" → 换来换去永远是第一首的标题；
  现在由**调用方**记住"上次自动填的值"，只在字段为空或仍等于旧自动值时覆盖
  （不擦用户手输的，但换歌一定会更新）。本模块只提供"猜什么"，
  不负责"要不要写进去"。

不导入 Qt，可直接单测。
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Tuple

# 明确属于"歌名"的标记
_TITLE_HINTS = re.compile(
    r"(\(live\)|（live）|\(live|live版|现场|remix|\(remix|feat\.|ft\.|"
    r"伴奏|纯音乐|inst\.|demo|mv版|完整版|tv\s*size|acoustic|ver\.)", re.I)
# "歌手"侧的名字分隔符
_ARTIST_SEPS = re.compile(r"[&、,，/／_·]|\s+and\s+|\s*×\s*")
# 常见的"这首歌是谁唱的"后缀噪声
_NOISE = re.compile(r"\s*[\[\(【（]\s*(官方|高音质|无损|动态歌词|歌词|完整版|"
                    r"1080p|4k|hi-?res|flac|mp3)\s*[\]\)】）]\s*", re.I)

_DASH = re.compile(r"\s*[-–—]\s*")


def clean_stem(path: str) -> str:
    """去掉目录、扩展名、以及"无损/动态歌词"这类噪声。"""
    stem = os.path.splitext(os.path.basename(str(path or "")))[0]
    stem = _NOISE.sub(" ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem


def split_title_artists(path: str) -> Tuple[str, List[str]]:
    """返回 ``(歌名, [艺人...])``。识别不出来就返回 ``(stem, [])``。"""
    stem = clean_stem(path)
    if not stem:
        return "", []

    parts = [p.strip() for p in _DASH.split(stem) if p.strip()]
    if len(parts) < 2:
        return stem, []

    left, right = parts[0], " - ".join(parts[1:])

    left_title = bool(_TITLE_HINTS.search(left))
    right_title = bool(_TITLE_HINTS.search(right))
    left_multi = len(_ARTIST_SEPS.findall(left)) >= 1
    right_multi = len(_ARTIST_SEPS.findall(right)) >= 1

    # 有明确的"歌名"标记 → 标记那侧是歌名
    if right_title and not left_title:
        title, artist_s = right, left
    elif left_title and not right_title:
        title, artist_s = left, right
    elif left_multi and not right_multi:
        title, artist_s = right, left
    elif right_multi and not left_multi:
        title, artist_s = left, right
    else:
        # 本质歧义 → 按音乐文件下载的通行约定：艺人 - 歌名
        title, artist_s = right, left

    artists = [a.strip() for a in _ARTIST_SEPS.split(artist_s) if a.strip()]
    return title.strip(), artists


def suggest(audio_path: str = "", lyrics_path: str = "") -> Dict[str, object]:
    """综合音频与歌词文件名给一组建议值。

    歌词文件名往往更"干净"（``在出发之前 - 陶典.lrc``），
    音频文件名常带一堆噪声，所以**两个都对一遍，取线索更多的那个**。
    """
    t1, a1 = split_title_artists(audio_path) if audio_path else ("", [])
    t2, a2 = split_title_artists(lyrics_path) if lyrics_path else ("", [])
    if t2 and (len(a2) > len(a1) or (a2 and not a1)):
        title, artists, src = t2, a2, "lyrics"
    elif t1:
        title, artists, src = t1, a1, "audio"
    else:
        title, artists, src = t2, a2, "lyrics"
    return {"title": title, "artists": artists, "source": src,
            "artists_text": "、".join(artists)}


def should_autofill(current: str, last_auto: str, keep_user_input: bool = True) -> bool:
    """要不要用新的自动值覆盖输入框？

    * 输入框为空 → 填
    * 输入框内容 == 上次自动填的 → 填（**换歌必须更新**）
    * 用户手输过（内容既非空也不等于旧自动值）→ 不填
    """
    cur = (current or "").strip()
    if not cur:
        return True
    if not keep_user_input:
        return True
    return cur == (last_auto or "").strip()
