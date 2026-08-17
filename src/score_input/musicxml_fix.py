"""修掉辨識引擎產出的 MusicXML 裡，會讓下游整個掛掉的違規寫法。

放成獨立模組是因為有兩個地方要用：`omr_engine` 在產出當下就修（讓寫到磁碟上的
檔案本身是合法的），`merge` 在讀進來時再修一次（處理不是這次跑出來的舊檔）。

一開始只修在 merge 裡，結果單頁的辨識結果直接餵給 `run.py analyze` 還是會炸 ——
修在「讀的人」身上就得每個讀的人都記得修，修在「寫的人」身上才是一次解決。
"""

from pathlib import Path

from lxml import etree


def fix_orphan_chords(root):
    """刪掉「掛在休止符後面」的 <chord/>。回傳修掉幾個。

    `<chord/>` 的意思是「跟前一個音同時發聲」，所以前面**必須**真的有一個音。
    homr 偶爾會在休止符後面接一個帶 <chord/> 的音符 —— 那是不合法的 MusicXML，
    而且 partitura 讀到就會炸：

        AttributeError: 'Rest' object has no attribute 'is_grace_chord'

    一個音就讓整份譜讀不進來、整次建構失敗，代價完全不成比例。
    這裡把 <chord/> 拿掉、音符留著，變成一個普通的音 ——
    跟 partitura 自己碰到「<chord/> 前面沒有任何音」時的處理一致。
    """
    fixed = 0
    for measure in root.iter("measure"):
        prev_pitched = None
        for element in measure:
            if element.tag in ("backup", "forward"):
                prev_pitched = None      # 時間跳走了，前一個音不再相鄰
                continue
            if element.tag != "note":
                continue

            chord = element.find("chord")
            is_rest = element.find("rest") is not None
            if chord is not None:
                if prev_pitched is None:
                    element.remove(chord)
                    fixed += 1
                else:
                    continue             # 合法的和弦音，不動、也不更新 prev
            prev_pitched = None if is_rest else element
    return fixed


def sanitize_file(path):
    """就地修一份 MusicXML。回傳修掉幾個地方；沒有要修就不重寫檔案。"""
    path = Path(path)
    try:
        parser = etree.XMLParser(remove_blank_text=False, recover=True,
                                 resolve_entities=False)
        tree = etree.parse(str(path), parser)
    except (OSError, etree.XMLSyntaxError):
        return 0        # 讀不了的檔留給呼叫端去報錯，這裡不是負責解釋的人

    fixed = fix_orphan_chords(tree.getroot())
    if fixed:
        tree.write(str(path), encoding="UTF-8", xml_declaration=True,
                   pretty_print=False)
    return fixed
