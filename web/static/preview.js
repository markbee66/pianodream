"use strict";
/* 辨識結果的可視化。

   要判斷 OMR 認得對不對，光看一個「27 小節 / 203 音符」的數字沒有用 ——
   要嘛看到音符的形狀、要嘛聽到聲音。這裡兩個都給：

     鋼琴捲軸   橫軸時間、縱軸音高，一眼看出旋律線走向對不對
     試聽       Web Audio 直接彈出來，聽起來不對就是認錯了（最快的驗證方式）
     落下預覽   跟 Unity 音遊一樣的落下式畫面，順便確認 chart 資料是對的

   一樣不引任何函式庫：畫圖用 canvas，聲音用 Web Audio 的振盪器。 */

const PV = {
  chart: null,
  level: null,
  audio: null,
  playing: false,
  startedAt: 0,
  offset: 0,
  raf: null,
  mode: "roll",
  scheduled: [],
};

/* 左右手的顏色以 CSS 的 --hand-r / --hand-l 為準（style.css），
   這樣圖例的小方塊跟畫布上的音符不會各走各的。這裡的值只是讀不到時的退路。
   DIM 是音符被彈過之後的淡色版，維持寫死 —— 那是配合上面兩個色調過的。 */
let HAND_COLOR = { R: "#2f6feb", L: "#e0821a" };
const HAND_COLOR_DIM = { R: "#7ba4f5", L: "#f0b877" };
const BLACK_KEYS = new Set([1, 3, 6, 8, 10]);
const PX_PER_SEC = 90;
const ROW = 7;          // 鋼琴捲軸裡每個半音的高度
const PAD = 26;

// ---------------------------------------------------------------------------

function pvEl(id) { return document.getElementById(id); }

async function loadPreview(level) {
  const box = pvEl("preview-panel");
  // 校對面板要跟著預覽一起收 —— 不然換到還沒建構的專案時，
  // 上一個專案的問題清單會留在畫面上，看起來像是這個專案的。
  const hideAll = () => { box.hidden = true; pvEl("proof-panel").hidden = true; };
  if (!state.status || !state.status.build) { hideAll(); return; }

  const levels = state.status.build.levels || [];
  if (!levels.length) { hideAll(); return; }

  box.hidden = false;
  renderLevelButtons(levels);
  PV.level = level || (PV.level && levels.includes(PV.level) ? PV.level : Math.max(...levels));

  stopPlayback();
  try {
    PV.chart = await api(
      `/api/projects/${encodeURIComponent(state.name)}/chart/${PV.level}`);
  } catch (err) {
    pvEl("preview-info").textContent = err.message;
    pvEl("proof-panel").hidden = true;
    return;
  }
  renderLevelButtons(levels);
  const bad = PV.chart.measures.filter((m) => m.ok === false).length;
  const total = PV.chart.measures.length;
  pvEl("preview-info").textContent =
    `${PV.chart.level_name}　${PV.chart.note_count} 音　` +
    `${PV.chart.duration_sec.toFixed(1)} 秒　${total} 小節　` +
    `BPM ${PV.chart.bpm}` +
    (bad ? `　⚠ 其中 ${bad} 節辨識不可靠` : "　✓ 每一節的拍數都對得上");
  draw();
  loadProblems();
}

function renderLevelButtons(levels) {
  const box = pvEl("level-buttons");
  box.innerHTML = "";
  levels.forEach((level) => {
    const b = document.createElement("button");
    b.textContent = `難度 ${level}`;
    if (level === PV.level) b.className = "primary";
    b.addEventListener("click", () => loadPreview(level));
    box.append(b);
  });
}

// ---------------------------------------------------------------------------
// 畫圖

function pitchRange() {
  const pitches = PV.chart.notes.map((n) => n.midi);
  const low = Math.min(...pitches) - 2;
  const high = Math.max(...pitches) + 2;
  return { low, high, span: high - low + 1 };
}

function draw() {
  if (!PV.chart) return;
  if (PV.mode === "roll") drawRoll();
  else drawFalling();
}

function canvasSetup(canvas, width, height) {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return ctx;
}

function themeColors() {
  const style = getComputedStyle(document.body);
  // 順便把手別顏色同步過來，圖例與音符才會是同一個顏色
  const r = style.getPropertyValue("--hand-r").trim();
  const l = style.getPropertyValue("--hand-l").trim();
  if (r && l) HAND_COLOR = { R: r, L: l };
  return {
    bg: style.getPropertyValue("--panel-2").trim() || "#f0f2f5",
    line: style.getPropertyValue("--line").trim() || "#d9dde3",
    ink: style.getPropertyValue("--ink").trim() || "#1b1d21",
    dim: style.getPropertyValue("--ink-dim").trim() || "#5c6370",
    bad: style.getPropertyValue("--bad").trim() || "#c02a2a",
  };
}

function drawRoll() {
  const { low, span } = pitchRange();
  const width = Math.max(600, PV.chart.duration_sec * PX_PER_SEC + PAD * 2);
  const height = span * ROW + PAD * 2;
  const ctx = canvasSetup(pvEl("preview-canvas"), width, height);
  const c = themeColors();

  ctx.fillStyle = c.bg;
  ctx.fillRect(0, 0, width, height);

  const y = (midi) => PAD + (low + span - 1 - midi) * ROW;
  const x = (sec) => PAD + sec * PX_PER_SEC;

  // 黑鍵的橫列鋪底色，這樣不用看座標軸也知道音高落在哪
  for (let midi = low; midi < low + span; midi++) {
    if (BLACK_KEYS.has(((midi % 12) + 12) % 12)) {
      ctx.fillStyle = c.line;
      ctx.globalAlpha = 0.35;
      ctx.fillRect(0, y(midi), width, ROW);
      ctx.globalAlpha = 1;
    }
    if (midi % 12 === 0) {   // 每個 C 畫一條線並標音名
      ctx.strokeStyle = c.line;
      ctx.beginPath();
      ctx.moveTo(0, y(midi) + ROW);
      ctx.lineTo(width, y(midi) + ROW);
      ctx.stroke();
      ctx.fillStyle = c.dim;
      ctx.font = "10px system-ui, sans-serif";
      ctx.fillText(`C${midi / 12 - 1}`, 3, y(midi) + ROW - 2);
    }
  }

  // 辨識不可靠的小節先鋪紅底。畫在音符**下面**，這樣不會蓋掉旋律線。
  // 一整段連續的壞小節會連成一塊，正好看得出「這一段整段不能信」。
  const measures = PV.chart.measures;
  measures.forEach((m, i) => {
    if (m.ok !== false) return;
    const next = measures[i + 1];
    const until = next ? next.t : PV.chart.duration_sec;
    ctx.fillStyle = c.bad;
    ctx.globalAlpha = 0.16;
    ctx.fillRect(x(m.t), PAD - 12, Math.max(2, (until - m.t) * PX_PER_SEC), height - PAD * 2 + 16);
    ctx.globalAlpha = 1;
  });

  // 小節線與編號
  ctx.font = "10px system-ui, sans-serif";
  measures.forEach((m) => {
    ctx.strokeStyle = m.ok === false ? c.bad : c.line;
    ctx.beginPath();
    ctx.moveTo(x(m.t), PAD - 12);
    ctx.lineTo(x(m.t), height - PAD + 4);
    ctx.stroke();
    if (m.n % 2 === 1 || m.ok === false) {
      ctx.fillStyle = m.ok === false ? c.bad : c.dim;
      ctx.fillText(m.n, x(m.t) + 2, PAD - 14);
    }
  });

  PV.chart.notes.forEach((n) => {
    ctx.fillStyle = HAND_COLOR[n.hand] || HAND_COLOR.R;
    const w = Math.max(3, n.d * PX_PER_SEC - 1.5);
    ctx.fillRect(x(n.t), y(n.midi) + 1, w, ROW - 2);
  });

  drawPlayhead(ctx, height);
}

function drawPlayhead(ctx, height) {
  const t = currentTime();
  if (t <= 0) return;
  const px = PAD + t * PX_PER_SEC;
  ctx.strokeStyle = "#e04b4b";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(px, 0);
  ctx.lineTo(px, height);
  ctx.stroke();
  ctx.lineWidth = 1;

  // 播放時讓畫面跟著跑
  const wrap = pvEl("preview-scroll");
  if (PV.playing && wrap) {
    const target = px - wrap.clientWidth * 0.35;
    if (Math.abs(wrap.scrollLeft - target) > 40) wrap.scrollLeft = target;
  }
}

// 落下式預覽：跟 Unity 音遊的畫面一樣，音符從上往下掉到鍵盤線
const FALL_SECONDS = 2.2;    // 音符從出現到落到鍵盤線要幾秒

function drawFalling() {
  const wrap = pvEl("preview-scroll");
  const width = Math.max(560, wrap.clientWidth - 4);
  const height = 460;
  const ctx = canvasSetup(pvEl("preview-canvas"), width, height);
  const c = themeColors();

  const { low, span } = pitchRange();
  const keyW = width / span;
  const hitY = height - 70;

  ctx.fillStyle = c.bg;
  ctx.fillRect(0, 0, width, height);

  const keyX = (midi) => (midi - low) * keyW;

  // 鍵盤。顏色寫死不用主題變數 —— 鋼琴鍵就是黑白的，
  // 用會隨深淺色翻轉的變數會讓黑鍵在深色模式下變成白的。
  for (let midi = low; midi < low + span; midi++) {
    const black = BLACK_KEYS.has(((midi % 12) + 12) % 12);
    ctx.fillStyle = black ? "#2b2f36" : "#f2f3f5";
    ctx.strokeStyle = "#9aa1ab";
    ctx.fillRect(keyX(midi), hitY, keyW - 1, height - hitY);
    ctx.strokeRect(keyX(midi), hitY, keyW - 1, height - hitY);
  }
  ctx.strokeStyle = "#e04b4b";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(0, hitY);
  ctx.lineTo(width, hitY);
  ctx.stroke();
  ctx.lineWidth = 1;

  // 正在落下的音符
  const now = currentTime();
  PV.chart.notes.forEach((n) => {
    const remaining = n.t - now;
    if (remaining > FALL_SECONDS || remaining < -n.d) return;
    const y = hitY - (remaining / FALL_SECONDS) * hitY;
    const h = Math.max(6, (n.d / FALL_SECONDS) * hitY);
    const hit = remaining <= 0;
    ctx.fillStyle = hit ? HAND_COLOR_DIM[n.hand] : HAND_COLOR[n.hand];
    ctx.fillRect(keyX(n.midi) + 1, y - h, keyW - 3, h);
  });

  ctx.fillStyle = c.dim;
  ctx.font = "12px system-ui, sans-serif";
  ctx.fillText(`${now.toFixed(1)} / ${PV.chart.duration_sec.toFixed(1)} 秒`, 8, 18);
  if (!PV.playing) ctx.fillText("按「試聽」看音符落下", 8, 36);
}

// ---------------------------------------------------------------------------
// 試聽
// 用振盪器疊三個泛音做出接近鋼琴的音色。不求好聽，求「音高聽得出對不對」。

function audioContext() {
  if (!PV.audio) PV.audio = new (window.AudioContext || window.webkitAudioContext)();
  return PV.audio;
}

function currentTime() {
  if (!PV.playing) return PV.offset;
  return PV.offset + (audioContext().currentTime - PV.startedAt);
}

function scheduleNote(ctx, note, when) {
  const freq = 440 * Math.pow(2, (note.midi - 69) / 12);
  const gain = ctx.createGain();
  gain.connect(ctx.destination);

  const peak = note.hand === "L" ? 0.10 : 0.14;   // 左手輕一點，旋律才聽得清楚
  const dur = Math.min(Math.max(note.d, 0.16), 2.4);
  gain.gain.setValueAtTime(0.0001, when);
  gain.gain.exponentialRampToValueAtTime(peak, when + 0.012);
  gain.gain.exponentialRampToValueAtTime(0.0001, when + dur);

  [[1, 1], [2, 0.34], [3, 0.13]].forEach(([mult, amp]) => {
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    osc.type = "triangle";
    osc.frequency.value = freq * mult;
    g.gain.value = amp;
    osc.connect(g).connect(gain);
    osc.start(when);
    osc.stop(when + dur + 0.05);
    // 記下停止時間，播完的節點才清得掉（見 scheduleAhead）
    osc.__stopAt = when + dur + 0.05;
    PV.scheduled.push(osc);
  });
}

// 一次只往前排這麼多秒的音符。夠讓排程穩定，又不會一次建太多節點。
const SCHEDULE_AHEAD = 4.0;

/// 把 [已排到的位置, 現在時間 + SCHEDULE_AHEAD] 之間的音符排進去。
/// 播放迴圈每一幀呼叫一次，所以節點是「用多少建多少」。
function scheduleAhead(ctx) {
  if (!PV.playing && PV.scheduledUpTo === undefined) return;
  const now = currentTime();
  const until = now + SCHEDULE_AHEAD;
  if (PV.scheduledUpTo >= until) return;

  PV.chart.notes.forEach((n) => {
    if (n.t < PV.scheduledUpTo || n.t >= until) return;
    scheduleNote(ctx, n, PV.startedAt + (n.t - PV.offset));
  });
  PV.scheduledUpTo = until;

  // 已經播完的節點留著只會一直堆積 —— 播 3 分鐘的曲子會累積上萬個
  PV.scheduled = PV.scheduled.filter((osc) => osc.__stopAt === undefined
                                     || osc.__stopAt > ctx.currentTime);
}

function startPlayback(from) {
  if (!PV.chart) return;
  const ctx = audioContext();
  if (ctx.state === "suspended") ctx.resume();
  stopPlayback(true);

  PV.offset = from !== undefined ? from : PV.offset;
  if (PV.offset >= PV.chart.duration_sec - 0.05) PV.offset = 0;
  PV.startedAt = ctx.currentTime;
  PV.playing = true;

  // **不要一次排完整首。** 每個音會建 3 個 oscillator，〈Rush E〉有 2576 個音
  // 就是 7728 個節點同時建立 —— 瀏覽器的 Web Audio 撐不住，會卡死或直接沒聲音。
  // 改成只排前面一小段，其餘在播放迴圈裡邊播邊補。
  PV.scheduledUpTo = PV.offset;
  scheduleAhead(ctx);

  pvEl("btn-play").textContent = "停止";
  loop();
}

function stopPlayback(keepOffset) {
  PV.scheduled.forEach((osc) => { try { osc.stop(); } catch { /* 已經停了 */ } });
  PV.scheduled = [];
  if (PV.playing && !keepOffset) PV.offset = currentTime();
  PV.playing = false;
  cancelAnimationFrame(PV.raf);
  const btn = pvEl("btn-play");
  if (btn) btn.textContent = "試聽";
  draw();
}

function loop() {
  PV.raf = requestAnimationFrame(() => {
    if (!PV.playing) return;
    scheduleAhead(audioContext());
    if (currentTime() >= PV.chart.duration_sec + 0.3) {
      PV.offset = 0;
      stopPlayback(true);
      return;
    }
    draw();
    loop();
  });
}

// ---------------------------------------------------------------------------
// 逐小節校對
//
// 機器修不了這些。實測過讓程式自動補拍數，只有 15% 的壞小節推得出唯一解 ——
// 剩下 85% 有好幾種同樣合理的改法，機器無從選擇，因為它只看得到自己讀錯的結果。
// 但人只要看一眼原譜就知道是哪個音。所以這裡不猜，直接把原譜攤開給人看。
// ---------------------------------------------------------------------------

const MIDI_NAMES = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"];

function noteName(midi) {
  return MIDI_NAMES[((midi % 12) + 12) % 12] + (Math.floor(midi / 12) - 1);
}

async function loadProblems() {
  const panel = pvEl("proof-panel");
  const list = pvEl("proof-list");
  let data;
  try {
    data = await api(
      `/api/projects/${encodeURIComponent(state.name)}/problems/${PV.level}`);
  } catch {
    panel.hidden = true;
    return;
  }

  panel.hidden = false;
  list.innerHTML = "";
  pvEl("proof-detail").innerHTML =
    `<div class="empty"><b>點左邊任一小節</b>` +
    `<span>這裡會顯示譜上原本印的樣子。</span></div>`;

  if (!data.bad) {
    pvEl("proof-summary").textContent =
      `全部 ${data.total} 小節的拍數都對得上拍號，沒有需要校對的地方。`;
    return;
  }
  pvEl("proof-summary").textContent =
    `${data.total} 小節裡有 ${data.bad} 節的拍數對不上拍號` +
    `（${Math.round(data.bad / data.total * 100)}%）。這些地方的節奏不能信，音高通常還是對的。`;

  data.measures.forEach((m) => {
    const li = document.createElement("li");
    li.className = "proof-item";
    li.innerHTML = `<b>第 ${m.n} 小節</b><span>${m.reason}</span>`;
    li.addEventListener("click", () => {
      [...list.children].forEach((n) => n.classList.remove("on"));
      li.classList.add("on");
      showProblem(m);
    });
    list.appendChild(li);
  });
}

function showProblem(m) {
  const url = `/api/projects/${encodeURIComponent(state.name)}` +
              `/measure-image/${PV.level}/${m.n}`;
  const read = m.notes.length
    ? m.notes.map((n) => `${noteName(n.midi)}<i>${n.d.toFixed(2)}s</i>`).join("　")
    : "（這一小節 AI 完全沒讀到音符）";

  pvEl("proof-detail").innerHTML = `
    <h3>第 ${m.n} 小節</h3>
    <p class="proof-reason">${m.reason}<br><span>${m.hint}</span></p>
    <figure>
      <img src="${url}" alt="第 ${m.n} 小節的原譜">
      <figcaption>譜上原本印的樣子。小節編號可能和譜上印的差一號 ——
        弱起小節出版社通常不算，我們算。</figcaption>
    </figure>
    <p class="proof-read"><b>AI 讀成 ${m.notes.length} 個音：</b><br>${read}</p>
    <button class="proof-jump">從這一小節試聽</button>`;

  pvEl("proof-detail").querySelector(".proof-jump")
    .addEventListener("click", () => startPlayback(Math.max(0, m.t - 0.5)));
}

function setMode(mode) {
  PV.mode = mode;
  pvEl("mode-roll").className = mode === "roll" ? "primary" : "";
  pvEl("mode-fall").className = mode === "fall" ? "primary" : "";
  pvEl("preview-scroll").classList.toggle("centered", mode === "fall");
  draw();
}

function initPreview() {
  pvEl("btn-play").addEventListener("click", () => {
    if (PV.playing) { stopPlayback(); } else { startPlayback(); }
  });
  pvEl("btn-restart").addEventListener("click", () => {
    PV.offset = 0;
    if (PV.playing) startPlayback(0); else draw();
  });
  pvEl("mode-roll").addEventListener("click", () => setMode("roll"));
  pvEl("mode-fall").addEventListener("click", () => setMode("fall"));

  // 點鋼琴捲軸任一處跳到那個時間點
  pvEl("preview-canvas").addEventListener("click", (e) => {
    if (PV.mode !== "roll" || !PV.chart) return;
    const rect = e.target.getBoundingClientRect();
    const sec = Math.max(0, (e.clientX - rect.left - PAD) / PX_PER_SEC);
    if (PV.playing) startPlayback(sec); else { PV.offset = sec; draw(); }
  });

  window.addEventListener("resize", () => { if (PV.mode === "fall") draw(); });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", draw);
  // 手動切換深淺色時 app.js 會發這個事件。canvas 是畫死的像素，
  // 不像 DOM 會自己跟著 CSS 變數改，一定要重畫。
  window.addEventListener("themechange", draw);
}

initPreview();
// app.js 先載入並已經跑過一次 render()，那時 loadPreview 還不存在，所以這裡補一次
loadPreview();
