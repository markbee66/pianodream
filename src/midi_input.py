"""電子琴 (USB-MIDI) 輸入：列出裝置、即時錄音成 MIDI 檔。

數位鋼琴直接送 MIDI，時間精度是毫秒級、力度 (velocity) 也直接拿得到，
不需要做音訊轉譜，這是最準的一條路。
"""

import time

import mido


def list_ports():
    """列出可用的 MIDI 輸入裝置名稱。"""
    try:
        return mido.get_input_names()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"無法列出 MIDI 裝置（{exc}）。請確認已安裝 python-rtmidi 且電子琴已用 USB 連上電腦。"
        ) from exc


def pick_port(name=None):
    """挑一個輸入埠：指定名稱做子字串比對，否則取第一個。"""
    ports = list_ports()
    if not ports:
        raise RuntimeError("找不到任何 MIDI 輸入裝置。請把電子琴用 USB 接上並開機。")
    if name is None:
        return ports[0]
    for port in ports:
        if name.lower() in port.lower():
            return port
    raise RuntimeError(f"找不到符合 '{name}' 的裝置。可用的有：{ports}")


def monitor(port_name=None, duration=30.0):
    """連線測試：把琴上的每個動作即時印出來。

    插上電子琴後先跑這個，確認音高、力度、踏板都有進來，再開始錄音。
    """
    port_name = pick_port(port_name)
    print(f"[MIDI] 使用裝置：{port_name}")
    print(f"[MIDI] 請隨便彈幾個音、也踩一下延音踏板。{duration:.0f} 秒後或 Ctrl+C 結束。\n")

    seen = {"note": 0, "pedal": 0}
    start = time.perf_counter()

    try:
        with mido.open_input(port_name) as port:
            while time.perf_counter() - start < duration:
                msg = port.poll()
                if msg is None:
                    time.sleep(0.001)
                    continue
                elapsed = time.perf_counter() - start

                if msg.type == "note_on" and msg.velocity > 0:
                    seen["note"] += 1
                    print(
                        f"  {elapsed:6.2f}s  彈下 {_pitch_name(msg.note):<4} "
                        f"(MIDI {msg.note:3d})  力度 {msg.velocity:3d}"
                    )
                elif msg.type == "control_change" and msg.control == 64:
                    seen["pedal"] += 1
                    print(f"  {elapsed:6.2f}s  踏板 {'踩下' if msg.value >= 64 else '放開'} ({msg.value})")
    except KeyboardInterrupt:
        pass

    print(f"\n[MIDI] 結束：收到 {seen['note']} 個音、{seen['pedal']} 個踏板事件。")
    if seen["note"] == 0:
        print("[MIDI] 沒收到音符 —— 檢查琴是否開機、是否選到正確裝置、USB 線是否為資料線。")
    elif seen["pedal"] == 0:
        print("[MIDI] 沒收到踏板訊號 —— 踏板評估會自動略過，不影響其他維度。")
    else:
        print("[MIDI] 一切正常，可以開始錄音了。")
    return seen


PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _pitch_name(midi_pitch):
    return f"{PITCH_NAMES[int(midi_pitch) % 12]}{int(midi_pitch) // 12 - 1}"


def record(out_path, port_name=None, silence_timeout=4.0, max_seconds=600.0, on_event=None):
    """從電子琴錄一段演奏，存成 MIDI 檔。

    第一個音符按下才開始計時；連續 silence_timeout 秒沒有任何事件就自動結束，
    也可以隨時 Ctrl+C 停止。on_event(msg, elapsed) 可用來做即時回饋。
    """
    port_name = pick_port(port_name)
    print(f"[MIDI] 使用裝置：{port_name}")
    print(f"[MIDI] 開始彈奏即開始錄音；停手 {silence_timeout:.0f} 秒或按 Ctrl+C 結束。")

    events = []          # (絕對秒數, mido.Message)
    start = None
    last_activity = None

    try:
        with mido.open_input(port_name) as port:
            while True:
                msg = port.poll()
                now = time.perf_counter()

                if msg is None:
                    if start is not None and now - last_activity > silence_timeout:
                        print("[MIDI] 偵測到停手，結束錄音。")
                        break
                    if start is not None and now - start > max_seconds:
                        print("[MIDI] 達到時間上限，結束錄音。")
                        break
                    time.sleep(0.001)
                    continue

                if msg.type not in ("note_on", "note_off", "control_change"):
                    continue

                if start is None:
                    if msg.type == "note_on" and msg.velocity > 0:
                        start = now
                        print("[MIDI] 開始錄音。")
                    else:
                        continue

                last_activity = now
                elapsed = now - start
                events.append((elapsed, msg))
                if on_event is not None:
                    on_event(msg, elapsed)

    except KeyboardInterrupt:
        print("\n[MIDI] 使用者中止，儲存已錄到的內容。")

    if not events:
        raise RuntimeError("沒有錄到任何音符。")

    write_midi(events, out_path)
    print(f"[MIDI] 已存檔：{out_path}（{len(events)} 個事件，{events[-1][0]:.1f} 秒）")
    return out_path


def write_midi(events, out_path, ticks_per_beat=480, bpm=120):
    """把 (秒數, message) 序列寫成單軌 MIDI 檔。

    以固定 120 BPM 當時間基準，實際的 rubato 完整保留在 tick 間距裡，
    下游用 partitura 讀出來就是真實的演奏秒數。
    """
    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0))

    sec_per_tick = (60.0 / bpm) / ticks_per_beat
    prev_tick = 0
    for elapsed, msg in events:
        tick = int(round(elapsed / sec_per_tick))
        delta = max(tick - prev_tick, 0)
        prev_tick = tick
        track.append(msg.copy(time=delta))

    mid.save(str(out_path))
    return out_path
