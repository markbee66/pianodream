"""一小節到底有多長 —— Gate B 與修復層共用的同一把尺。

`validate.py` 與 `repair.py` 本來各寫了一份一模一樣的游標模型
（`_measure_duration` 與 `_duration_of`）。兩把尺量出不同的數字是最難查的那種
bug（`dedupe_time_signatures()` 的註解就記著一次），所以合成一份。

## 兩種量法，缺一不可

**游標**（MusicXML 的正式語意）：`<note>` 把游標往前推，`<chord>` 跟前一個音同時
發聲所以不推，`<backup>` / `<forward>` 直接搬游標。這是規格說的算法。

**逐聲部總和**：每個 `<voice>` 自己的音符時值加起來。

正常的檔案兩者一致。但 homr 產出的大譜表是**一個音一個音交錯排**的
（上行一個音 → backup → 下行一個音），backup 的位置常常不對，於是游標會走出
一個毫無意義的數字，而各聲部自己其實都剛好填滿：

    〈山魔王的宮殿〉第 48 小節　voice 1 = 4.0 拍　voice 5 = 4.0 拍　游標 = 4.5 拍

那一小節**沒有任何問題**，兩個聲部都剛好是 4/4 要求的 4 拍，卻被判成拍數不對。
全 12 首量下來，534 個「游標判壞」的小節裡有 44 個（8%）是這種情形，
〈Rush E〉一首就佔 19 個。

所以 `is_complete()` 的判準是：**游標對，或是每一個聲部各自都剛好對。**
只會讓誤判變少，不會多判任何一個小節有問題 —— 各聲部都填滿的小節，
在音樂上就是完整的；游標對不上只表示 backup 擺錯位置，那是排版的事。
"""

TOLERANCE = 0.02          # 拍數比對的容許誤差（四分音符）


def measure_duration(measure, divisions):
    """用游標模型算這一小節佔了多少四分音符。

    不能單純把所有音符時值加起來 —— 和弦音不推進游標，backup / forward 會搬游標。
    """
    cursor = longest = 0.0
    for element in measure:
        if element.tag == "note":
            if element.find("chord") is not None or element.find("grace") is not None:
                continue
            cursor += float(element.findtext("duration") or 0)
            longest = max(longest, cursor)
        elif element.tag == "backup":
            cursor -= float(element.findtext("duration") or 0)
        elif element.tag == "forward":
            cursor += float(element.findtext("duration") or 0)
            longest = max(longest, cursor)
    return longest / divisions if divisions else 0.0


def voice_notes(measure):
    """{voice: [會推進游標的音符]}，依文件順序。

    和弦音與裝飾音不算 —— 它們跟前一個音同時發聲，不佔自己的時間。
    """
    voices = {}
    for note in measure.findall("note"):
        if note.find("chord") is not None or note.find("grace") is not None:
            continue
        voices.setdefault(note.findtext("voice") or "1", []).append(note)
    return voices


def voice_durations(measure, divisions):
    """{voice: 這個聲部在這一小節佔了幾個四分音符}。

    比「整個小節的長度對不對」更好定位：它直接指出是**哪一個聲部**出問題，
    而不是丟一個沒辦法往下追的總數。
    """
    if not divisions:
        return {}
    return {
        voice: sum(float(n.findtext("duration") or 0) for n in notes) / divisions
        for voice, notes in voice_notes(measure).items()
    }


def voices_agree(measure, divisions, expected, tolerance=TOLERANCE):
    """每一個聲部各自都剛好等於拍號要求嗎？沒有任何聲部時回 False。"""
    lengths = voice_durations(measure, divisions)
    return bool(lengths) and all(abs(v - expected) < tolerance for v in lengths.values())


def is_complete(measure, divisions, expected, tolerance=TOLERANCE):
    """這一小節的拍數對不對。游標對、或各聲部都各自對，都算對（理由見模組開頭）。"""
    if abs(measure_duration(measure, divisions) - expected) < tolerance:
        return True
    return voices_agree(measure, divisions, expected, tolerance)
