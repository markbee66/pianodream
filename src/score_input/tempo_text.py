"""從**文字**判斷速度：節拍器記號、義大利文術語、MusicXML 內建的欄位。

從 `tempo.py` 拆出來的。這一層不碰影像也不碰小節，純粹是「一段字串 -> BPM」，
所以 `tempo_page`（OCR 讀到的字）與 `tempo.from_musicxml`（檔案裡的 `<words>`）
都能共用同一份術語表。
"""

import re
from dataclasses import dataclass

# 速度術語 -> 大約的 BPM。義大利文為主，另收法文與德文常見的幾個。
# 數字取各家節拍器標示的中間值；這本來就是個範圍，只求量級對。
TEMPO_WORDS = {
    # 慢
    "grave": 40, "largo": 50, "larghetto": 63, "lento": 55, "adagio": 70,
    "adagietto": 75, "lentement": 55, "langsam": 60,
    # 中
    "andante": 90, "andantino": 95, "moderato": 110, "modéré": 110,
    "allegretto": 120, "mässig": 100, "massig": 100,
    # 快
    "allegro": 140, "vivace": 165, "vivo": 160, "presto": 185,
    "prestissimo": 200, "schnell": 150, "vif": 160,
    # 常見的英文
    "slow": 60, "moderate": 100, "fast": 150,
}

# 修飾語：會把基礎速度往上下調
TEMPO_MODIFIERS = {
    "molto": 1.15, "assai": 1.10, "con moto": 1.10, "con brio": 1.10,
    "non troppo": 0.92, "ma non troppo": 0.92, "moderato ma": 1.0,
    "meno": 0.88, "poco": 0.95, "sostenuto": 0.90,
}

# 節拍器記號。音符符頭 OCR 出來很不穩（可能變成 J、d、o 或整個消失），
# 所以只認「= 數字」這個最穩定的部分。
_METRONOME = re.compile(r"[=＝]\s*(\d{2,3})")

# 合理範圍：低於 30 或高於 300 的多半是把小節號、指法數字誤讀成速度
BPM_MIN, BPM_MAX = 30, 300


@dataclass
class TempoResult:
    bpm: float = None            # None = 偵測失敗，要問使用者
    source: str = "none"         # metronome / words / notation / musicxml / none
    evidence: str = ""           # 依據什麼判斷的，要能讓使用者自己核對
    confidence: float = 0.0      # 0–1，只用來排序與決定要不要問

    @property
    def ok(self):
        return self.bpm is not None

    def as_dict(self):
        return {"bpm": self.bpm, "source": self.source,
                "evidence": self.evidence, "confidence": round(self.confidence, 2)}

    def describe(self):
        if not self.ok:
            return "偵測不到速度"
        label = {
            "metronome": "譜上的節拍器記號",
            "words": "譜上的速度術語",
            "notation": "記譜檔指定的",
            "musicxml": "樂譜檔內建的",
        }.get(self.source, self.source)
        return f"{self.bpm:g} BPM（{label}：{self.evidence}）"


def from_text(text):
    """從一段文字裡找速度。先找節拍器記號，找不到才查術語表。"""
    if not text:
        return TempoResult()

    m = _METRONOME.search(text)
    if m:
        bpm = _sane(m.group(1))
        if bpm:
            return TempoResult(bpm, "metronome", text.strip()[:40], 0.95)

    lowered = text.lower()
    for word, base in sorted(TEMPO_WORDS.items(), key=lambda kv: -len(kv[0])):
        if word not in lowered:
            continue
        bpm = float(base)
        applied = []
        for mod, factor in TEMPO_MODIFIERS.items():
            if mod in lowered:
                bpm *= factor
                applied.append(mod)
        note = text.strip()[:40]
        return TempoResult(round(bpm), "words", note, 0.6 if applied else 0.55)

    return TempoResult()


def from_musicxml(path):
    """樂譜檔自己寫的速度。MuseScore 匯出的譜會有；homr 產的沒有。"""
    from lxml import etree

    try:
        root = etree.parse(str(path), etree.XMLParser(recover=True)).getroot()
    except (OSError, etree.XMLSyntaxError):
        return TempoResult()

    # <sound tempo="120"> 是最直接的
    for sound in root.iter("sound"):
        value = sound.get("tempo")
        if value:
            bpm = _sane(value)
            if bpm:
                return TempoResult(bpm, "musicxml", f'sound tempo="{value}"', 1.0)

    # <metronome><beat-unit>quarter</beat-unit><per-minute>96</per-minute>
    for metro in root.iter("metronome"):
        per = metro.findtext("per-minute")
        unit = (metro.findtext("beat-unit") or "quarter").strip()
        bpm = _sane(per)
        if not bpm:
            continue
        # per-minute 是「以 beat-unit 為單位」，統一換算成每分鐘幾個四分音符
        factor = {"whole": 4, "half": 2, "quarter": 1,
                  "eighth": 0.5, "16th": 0.25}.get(unit, 1)
        if metro.find("beat-unit-dot") is not None:
            factor *= 1.5
        return TempoResult(bpm * factor, "musicxml",
                           f"metronome {unit}={per}", 1.0)

    # 有些檔案把速度寫成一般文字
    for words in root.iter("words"):
        result = from_text(words.text or "")
        if result.ok:
            result.source = "musicxml"
            return result

    return TempoResult()


def _sane(value):
    try:
        bpm = float(value)
    except (TypeError, ValueError):
        return None
    return bpm if BPM_MIN <= bpm <= BPM_MAX else None
