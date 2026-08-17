"""產生示範資料：一份標準樂譜 MIDI + 一份「刻意彈壞」的演奏 MIDI。

用途是在還沒接上電子琴之前，就能驗證整條分析流程是否正確 ——
因為錯誤是我們自己注入的，可以檢查系統有沒有把它們抓出來。

用法：python tools/make_demo.py
"""

import random
from pathlib import Path

import mido

TPB = 480          # ticks per beat
BPM = 100
ROOT = Path(__file__).resolve().parent.parent


def build_piece():
    """回傳 (譜面音符列表)。每個音符 = (起始拍, 長度拍, 音高, 力度)。

    8 小節 4/4：右手級進旋律，左手每小節第 1、3 拍的三和弦。
    """
    melody = [
        # (拍, 長度, 音高)
        (0.0, 1.0, 72), (1.0, 1.0, 74), (2.0, 1.0, 76), (3.0, 1.0, 77),
        (4.0, 2.0, 79), (6.0, 1.0, 77), (7.0, 1.0, 76),
        (8.0, 0.5, 74), (8.5, 0.5, 76), (9.0, 0.5, 77), (9.5, 0.5, 79),
        (10.0, 1.0, 81), (11.0, 1.0, 79),
        (12.0, 2.0, 77), (14.0, 2.0, 76),
        (16.0, 1.0, 74), (17.0, 1.0, 76), (18.0, 1.0, 77), (19.0, 1.0, 79),
        (20.0, 0.5, 81), (20.5, 0.5, 83), (21.0, 1.0, 84), (22.0, 2.0, 83),
        (24.0, 1.0, 81), (25.0, 1.0, 79), (26.0, 1.0, 77), (27.0, 1.0, 76),
        (28.0, 4.0, 72),
    ]

    chords = {  # 每小節 (第1拍和弦, 第3拍和弦)
        0: ([48, 52, 55], [48, 52, 55]),
        1: ([53, 57, 60], [53, 57, 60]),
        2: ([50, 53, 57], [55, 59, 62]),
        3: ([48, 52, 55], [48, 52, 55]),
        4: ([53, 57, 60], [53, 57, 60]),
        5: ([55, 59, 62], [55, 59, 62]),
        6: ([50, 53, 57], [55, 59, 62]),
        7: ([48, 52, 55], [48, 52, 55]),
    }

    notes = []
    for beat, dur, pitch in melody:
        notes.append((beat, dur, pitch, 72))

    for bar, (c1, c3) in chords.items():
        for offset, chord in ((0.0, c1), (2.0, c3)):
            for pitch in chord:
                notes.append((bar * 4 + offset, 2.0, pitch, 58))

    notes.sort(key=lambda n: (n[0], n[2]))
    return notes


def degrade(notes, seed=42):
    """把標準譜面「彈成學生的樣子」，注入可預期的錯誤。

    注入內容（分析器應該要能抓到這些）：
      1. 全域起音抖動 ~25 ms
      2. 第 5–6 小節趕拍（速度變快 18%）
      3. 漏彈 1 個音
      4. 多彈 1 個音（隔壁鍵）
      5. 和弦起音散開 ~35 ms（滾奏感）
      6. 左手比右手大聲（旋律被蓋掉）
      7. 力度層次過於平板
    """
    rng = random.Random(seed)
    sec_per_beat = 60.0 / BPM

    played = []
    dropped_index = None
    for i, (beat, dur, pitch, vel) in enumerate(notes):
        bar = int(beat // 4)

        # (3) 漏彈：第 3 小節的第一個旋律音
        if dropped_index is None and pitch >= 72 and bar == 3:
            dropped_index = i
            continue

        # (2) 第 5–6 小節趕拍：時間軸壓縮
        if bar >= 5:
            onset_sec = (5 * 4) * sec_per_beat + (beat - 5 * 4) * sec_per_beat * 0.82
        else:
            onset_sec = beat * sec_per_beat

        # (1) 起音抖動
        onset_sec += rng.gauss(0, 0.025)

        # (5) 和弦散開：低音先下，高音慢一點
        is_chord_note = pitch < 60
        if is_chord_note:
            onset_sec += (pitch % 12) / 12.0 * 0.035

        # (6)(7) 左手偏大聲、整體力度平板
        if pitch < 60:
            velocity = int(rng.gauss(76, 4))     # 左手伴奏太響
        else:
            velocity = int(rng.gauss(70, 4))     # 右手旋律沒突出、變化小

        duration_sec = dur * sec_per_beat * rng.uniform(0.55, 0.75)  # 偏斷奏且不一致

        played.append((onset_sec, duration_sec, pitch, max(1, min(127, velocity))))

    # (4) 多彈一個音：第 2 小節誤觸半音
    played.append((2 * 4 * sec_per_beat + 1.05, 0.25, 75, 66))

    played.sort(key=lambda n: n[0])
    return played


def write_score_midi(notes, path):
    """譜面 MIDI：完全量化、力度統一。"""
    mid = mido.MidiFile(ticks_per_beat=TPB)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(BPM), time=0))
    track.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))

    events = []
    for beat, dur, pitch, vel in notes:
        events.append((int(beat * TPB), "note_on", pitch, vel))
        events.append((int((beat + dur) * TPB), "note_off", pitch, 0))
    _flush(events, track)

    path.parent.mkdir(parents=True, exist_ok=True)
    mid.save(str(path))
    return path


def write_performance_midi(played, path):
    """演奏 MIDI：真實秒數寫成 tick，並加上踏板事件。"""
    mid = mido.MidiFile(ticks_per_beat=TPB)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(BPM), time=0))

    sec_per_tick = (60.0 / BPM) / TPB
    events = []
    for onset, dur, pitch, vel in played:
        events.append((int(onset / sec_per_tick), "note_on", pitch, vel))
        events.append((int((onset + dur) / sec_per_tick), "note_off", pitch, 0))

    # 踏板：每兩小節換一次
    total_sec = max(o + d for o, d, _, _ in played)
    t = 0.0
    while t < total_sec:
        events.append((int(t / sec_per_tick), "cc64", 100, 0))
        events.append((int(min(t + 4.6, total_sec) / sec_per_tick), "cc64", 0, 0))
        t += 4.8
    _flush(events, track)

    path.parent.mkdir(parents=True, exist_ok=True)
    mid.save(str(path))
    return path


def _flush(events, track):
    events.sort(key=lambda e: (e[0], 0 if e[1] == "note_off" else 1))
    prev = 0
    for tick, kind, a, b in events:
        delta = max(tick - prev, 0)
        prev = tick
        if kind == "note_on":
            track.append(mido.Message("note_on", note=a, velocity=b, time=delta))
        elif kind == "note_off":
            track.append(mido.Message("note_off", note=a, velocity=0, time=delta))
        else:
            track.append(mido.Message("control_change", control=64, value=a, time=delta))


def main():
    notes = build_piece()
    played = degrade(notes)

    score_path = write_score_midi(notes, ROOT / "data" / "scores" / "demo_score.mid")
    perf_path = write_performance_midi(
        played, ROOT / "data" / "performances" / "demo_performance.mid"
    )

    print(f"樂譜   : {score_path}  ({len(notes)} 音)")
    print(f"演奏   : {perf_path}  ({len(played)} 音)")
    print("\n注入的錯誤（分析器應該要抓到）：")
    print("  1. 起音抖動 ~25 ms")
    print("  2. 第 5–6 小節趕拍（快 18%）")
    print("  3. 漏彈 1 個旋律音")
    print("  4. 多彈 1 個音 (D#5)")
    print("  5. 和弦散開 ~35 ms")
    print("  6. 左手比右手大聲")
    print("  7. 力度平板、觸鍵偏斷奏且不一致")


if __name__ == "__main__":
    main()
