"""把反覆記號展開成線性樂譜。

音遊與評分讀的都是「從頭到尾一條時間軸」，沒有跳回去的概念。譜上寫著反覆
但沒有展開的話，有反覆的曲子**只會彈一遍** —— 交接書「沒做完的」那一節記著
這件事，使用者回報的「結尾會誤讀」也是同一個原因。

實測 12 首裡有 4 首帶反覆記號，而且全部落在信心 0.97–1.00 的可用曲目：

    Alkan前奏曲    m4 backward, m10 backward
    Andre小奏鳴曲  m8 backward, m9 forward, m24 backward, m25 forward, m33 backward
    うまぴょい伝説    m54 backward, m58 backward
    士兵進行曲      m17 forward, m32 backward

**homr 的 location 不可信**：它把 forward 也寫成 `location="right"`（正確的寫法
是 `left`）。所以這裡只看 `direction`（forward / backward）與它在第幾小節，
不看 location。

一二號結尾（volta）**暫時不處理**：homr 的 `<ending>` 輸出是 0，而第二引擎
（Audiveris）在山魔王讀到的 8 個全部是 `start` 與 `discontinue` 落在同一小節、
編號都是 1，而那首根本沒有反覆記號 —— 那是誤判，不能拿來展開。
"""

from lxml import etree

#: 一段反覆最多展開幾次，避免結構讀錯時產生爆炸性的長度
MAX_REPEATS = 2
#: 展開後的小節數超過原本這個倍數就放棄，當作結構讀錯了
MAX_GROWTH = 3.0


def find_sections(part):
    """讀出反覆結構，回傳 [(起, 迄)] —— 這幾段要彈兩次（1 起算，含頭含尾）。

    只看 direction，不看 location（理由見模組開頭）。沒有 forward 就從
    上一段結束的下一小節開始 —— 那是「從頭反覆」的標準寫法。
    """
    marks = []
    for index, measure in enumerate(part.findall("measure"), start=1):
        for barline in measure.findall("barline"):
            repeat = barline.find("repeat")
            if repeat is not None and repeat.get("direction") in ("forward", "backward"):
                marks.append((index, repeat.get("direction")))

    sections, start = [], 1
    for index, direction in marks:
        if direction == "forward":
            start = index
        else:                       # backward：這一段到此結束
            if index >= start:
                sections.append((start, index))
            start = index + 1
    return sections


def expand(root):
    """就地展開反覆。回傳 (展開後小節數, 原本小節數, [每個小節來自原本第幾小節])。

    回傳的對照表是給檢討畫面用的：展開之後第 30 小節可能是照片上的第 12 小節，
    沒有這張表就圈不到正確的位置（小節框對照表是以原始編號建的）。
    """
    for part in root.findall("part"):
        measures = part.findall("measure")
        original = len(measures)
        sections = find_sections(part)
        if not sections:
            return original, original, list(range(1, original + 1))

        # 展開後的閱讀順序：一段一段走，反覆的段落走兩次
        order, cursor = [], 1
        for start, end in sections:
            order.extend(range(cursor, start))          # 反覆段之前照常
            for _ in range(MAX_REPEATS):
                order.extend(range(start, end + 1))
            cursor = end + 1
        order.extend(range(cursor, original + 1))

        if not order or len(order) > original * MAX_GROWTH:
            return original, original, list(range(1, original + 1))

        parent = measures[0].getparent()
        for measure in measures:
            parent.remove(measure)

        for position, source in enumerate(order, start=1):
            clone = _copy(measures[source - 1])
            clone.set("number", str(position))
            # 第二遍不要再帶反覆記號，否則讀取端可能又跳一次
            for barline in clone.findall("barline"):
                repeat = barline.find("repeat")
                if repeat is not None:
                    barline.remove(repeat)
                if len(barline) == 0:
                    clone.remove(barline)
            parent.append(clone)

        return len(order), original, order

    return 0, 0, []


def _copy(measure):
    """深拷貝一個小節。lxml 的元素不能重複掛在樹上，一定要複製。"""
    return etree.fromstring(etree.tostring(measure))


def expand_file(path):
    """就地展開一份 MusicXML，回傳 {expanded, original, order}。沒有反覆就不寫回。"""
    parser = etree.XMLParser(remove_blank_text=False, recover=True, resolve_entities=False)
    try:
        tree = etree.parse(str(path), parser)
    except (OSError, etree.XMLSyntaxError):
        return {"expanded": 0, "original": 0, "order": []}

    root = tree.getroot()
    expanded, original, order = expand(root)
    if expanded and expanded != original:
        tree.write(str(path), encoding="UTF-8", xml_declaration=True, pretty_print=False)
    return {"expanded": expanded, "original": original, "order": order}
