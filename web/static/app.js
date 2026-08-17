"use strict";
/* 樂譜輸入介面。
   刻意不引任何前端函式庫 —— 拖曳排序用瀏覽器原生的 HTML5 drag and drop 就夠，
   多一個 CDN 依賴反而讓這個離線工具在沒網路的時候壞掉。 */

const $ = (id) => document.getElementById(id);
const state = { name: null, status: null, help: null, polling: null, dragIndex: null };

const VERDICT = {
  ok:     { cls: "ok",     text: "可以使用" },
  warn:   { cls: "warn",   text: "可以用，但有問題" },
  reject: { cls: "reject", text: "不能用，請換掉這一項" },
};

const ACCEPT = {
  photo:  { formats: "JPG、PNG、PDF（PDF 會自動拆成一頁一張）",
            hint: "把樂譜每一頁拍清楚，整頁入鏡、正對著拍。上傳後系統會先檢查拍攝品質。" },
  jianpu: { formats: "純文字檔 .txt",
            hint: "用 1-7 寫的數字記譜。格式說明在「新增專案」視窗裡，也可以照下面的範例改。" },
  letter: { formats: "純文字檔 .txt",
            hint: "用 C-B 寫的字母記譜。格式說明在「新增專案」視窗裡。" },
};

// ---------------------------------------------------------------------------

async function api(path, options = {}) {
  const res = await fetch(path, options);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { error: text }; }
  if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
  return data;
}

function setStatus(el, message, isError) {
  el.textContent = message || "";
  el.classList.toggle("err", !!isError);
}

// 隱私模式下 localStorage 會直接拋例外，不能讓它擋住整個介面
function writePref(key, value) { try { localStorage.setItem(key, value); } catch { /* 忽略 */ } }

// ---------------------------------------------------------------------------
// 深色 / 淺色
//
// 沒有手動選過就跟隨系統（CSS 那邊用 prefers-color-scheme 處理），
// 選過之後 <html data-theme> 一律蓋過系統設定。

const THEME_KEY = "pianoai.theme";

function currentTheme() {
  return document.documentElement.dataset.theme ||
    (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  writePref(THEME_KEY, theme);
  updateThemeButton();
  // canvas 的顏色是 preview.js 讀 CSS 變數後畫上去的像素，不會自己跟著主題變。
  // 換主題之後不重畫的話，鋼琴捲軸會維持前一個主題的配色。
  window.dispatchEvent(new Event("themechange"));
}

function updateThemeButton() {
  const dark = currentTheme() === "dark";
  const btn = $("theme-toggle");
  btn.textContent = dark ? "☀" : "☾";
  btn.title = dark ? "切換成淺色" : "切換成深色";
}

// ---------------------------------------------------------------------------
// 專案

async function loadProjects(preferred) {
  const rows = await api("/api/projects");
  const select = $("project-select");
  select.innerHTML = "";
  // 第一項是空的佔位。沒有它的話 select 一定會停在某一首上，
  // 看起來像「已經選了它」，但畫面其實還在新增流程。
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = rows.length ? "— 開啟既有專案 —" : "— 還沒有專案 —";
  select.append(placeholder);
  rows.forEach((row) => {
    const option = document.createElement("option");
    option.value = row.name;
    option.textContent = `${row.name}（${row.count} 項${row.built ? "、已建構" : ""}）`;
    select.append(option);
  });

  // **只在明確指定時才開既有專案。**
  //
  // 以前一律自動選中排序第一個（現在是「Alkan前奏曲」），所以打開網頁看到的
  // 是別人的舊專案，而不是「開始加新譜」。而這個頁面是從 Unity 的
  // 「加入新樂譜」按鈕、或 加樂譜.bat 進來的 —— 來的人十之八九是要加新的，
  // 改既有的才是次要，右上角的下拉選單本來就在那裡。
  const target = preferred && rows.some((r) => r.name === preferred) ? preferred : null;
  $("app").hidden = !target;
  $("no-project").hidden = !!target;
  $("btn-delete").disabled = !target;
  if (target) { select.value = target; await selectProject(target); }
  else {
    state.name = null;
    // 下拉選單不要停在某一首上，那看起來像「已經選了它」
    select.value = "";
    renderWelcome(rows);
  }
}

/* 歡迎畫面的「或繼續之前的」。骨架在 index.html，這裡只補清單 ——
   第一次來的人看到的是流程說明，不是一排他沒看過的專案名。 */
function renderWelcome(rows) {
  $("welcome-photo-hint").textContent = ACCEPT.photo.hint;

  const box = $("welcome-existing");
  const list = $("welcome-list");
  list.innerHTML = "";
  box.hidden = rows.length === 0;
  rows.forEach((row) => {
    const b = document.createElement("button");
    const name = document.createElement("b");
    name.textContent = row.name;
    const meta = document.createElement("small");
    meta.textContent = `${row.count} 項${row.built ? "、已建構" : "、還沒辨識"}`;
    b.append(name, meta);
    b.addEventListener("click", () => loadProjects(row.name));
    list.append(b);
  });
}

async function selectProject(name) {
  state.name = name;
  state.status = await api(`/api/projects/${encodeURIComponent(name)}`);
  render();
  pollBuild(true);
}

async function refresh(status) {
  state.status = status || await api(`/api/projects/${encodeURIComponent(state.name)}`);
  render();
}

// ---------------------------------------------------------------------------
// 畫面

function render() {
  const status = state.status;
  if (!status) return;

  const accept = ACCEPT[status.source_type] || ACCEPT.photo;
  $("upload-hint").textContent = accept.hint;
  $("dz-formats").textContent = `可用格式：${accept.formats}`;
  $("file-input").accept = status.source_type === "photo"
    ? ".jpg,.jpeg,.png,.bmp,.tif,.tiff,.webp,.pdf" : ".txt,.jp,.jianpu";

  const list = $("item-list");
  list.innerHTML = "";
  status.items.forEach((item) => list.append(itemCard(item)));
  $("empty-note").hidden = status.items.length > 0;

  const c = status.counts;
  $("counts").textContent = status.items.length
    ? `共 ${c.total} 項：可用 ${c.ok}、注意 ${c.warn}、不能用 ${c.reject}` +
      (c.unchecked ? `、未檢查 ${c.unchecked}` : "")
    : "";

  $("btn-build").disabled = status.items.length === 0;
  if (status.build && !$("build-result").dataset.live) showBuild(status.build, null);

  renderSteps(status);

  // preview.js 是後面才載入的，第一次 render 時它還不存在
  if (typeof loadPreview === "function") loadPreview();
  else $("preview-panel").hidden = true;
}

/* 五個步驟現在走到哪了。全部從既有的 status 推出來，不需要新的 API。

   「還沒輪到」只是淡掉、不是鎖住 —— 想先往下看的人還是看得到，
   但第一次用的人視線會落在唯一沒淡掉的那一塊。 */
function renderSteps(status) {
  const built = !!(status.build && (status.build.levels || []).length);
  const hasItems = status.items.length > 0;
  const allChecked = hasItems && !status.counts.unchecked;
  const done = [hasItems, allChecked, built, built, built];

  // 第一個還沒完成的就是現在該做的；全部做完就停在最後一步
  let active = done.indexOf(false);
  if (active < 0) active = done.length - 1;

  ["step-1", "step-2", "step-3", "preview-panel", "proof-panel"].forEach((id, i) => {
    markStep($(id), done[i], i === active);
    markStep(document.querySelector(`.rail-step[data-step="${i + 1}"]`),
             done[i], i === active);
  });
}

function markStep(el, isDone, isActive) {
  if (!el) return;
  el.classList.toggle("step-active", isActive);
  el.classList.toggle("step-done", isDone && !isActive);
  el.classList.toggle("step-todo", !isDone && !isActive);
}

function itemCard(item) {
  const verdict = VERDICT[item.verdict] || { cls: "none", text: "尚未檢查" };
  const li = document.createElement("li");
  li.className = `item ${verdict.cls}`;
  li.draggable = true;
  li.dataset.index = item.index;

  const badge = document.createElement("div");
  badge.className = "badge";
  badge.textContent = item.index;

  const thumb = document.createElement(item.kind === "image" ? "img" : "div");
  thumb.className = "thumb";
  if (item.kind === "image") {
    thumb.src = `/api/projects/${encodeURIComponent(state.name)}/raw/${encodeURIComponent(item.file)}`;
    thumb.alt = "";
  } else {
    thumb.textContent = "記譜";
  }

  const main = document.createElement("div");
  main.className = "item-main";
  const name = document.createElement("div");
  name.className = "item-name";
  name.textContent = item.original_name;
  const msg = document.createElement("div");
  msg.className = `item-msg ${verdict.cls}`;
  msg.textContent = item.issues.length
    ? item.issues.map((i) => i.message).join("；")
    : verdict.text;
  // 卡片上只留兩行（見 .item-msg 的 line-clamp），滑過去要看得到完整那句
  msg.title = msg.textContent;
  main.append(name, msg);

  if (item.measures || item.notes) {
    const stats = document.createElement("div");
    stats.className = "item-stats";
    const bits = [];
    if (item.measures) bits.push(`${item.measures} 小節`);
    if (item.notes) bits.push(`${item.notes} 音符`);
    if (item.confidence !== null && item.confidence !== undefined) {
      bits.push(`信心 ${Number(item.confidence).toFixed(2)}`);
    }
    if (item.problems && item.problems.length) bits.push(`${item.problems.length} 個樂理問題`);
    stats.textContent = bits.join("　");
    main.append(stats);
  }

  const actions = document.createElement("div");
  actions.className = "item-actions";
  actions.append(
    button("看細節", () => openDetail(item)),
    button(item.kind === "image" ? "換一張" : "換檔案", () => replaceItem(item.index), "primary"),
    button("刪除", () => removeItem(item.index)),
  );

  li.append(badge, thumb, main, actions);
  attachDrag(li);
  return li;
}

function button(label, onClick, cls) {
  const b = document.createElement("button");
  b.textContent = label;
  if (cls) b.className = cls;
  b.addEventListener("click", (e) => { e.stopPropagation(); onClick(); });
  return b;
}

// ---------------------------------------------------------------------------
// 拖曳排序（原生 HTML5）

function attachDrag(li) {
  li.addEventListener("dragstart", (e) => {
    state.dragIndex = Number(li.dataset.index);
    li.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", li.dataset.index);
  });
  li.addEventListener("dragend", () => {
    li.classList.remove("dragging");
    document.querySelectorAll(".item").forEach((n) => n.classList.remove("drop-target"));
  });
  li.addEventListener("dragover", (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    li.classList.add("drop-target");
  });
  li.addEventListener("dragleave", () => li.classList.remove("drop-target"));
  li.addEventListener("drop", async (e) => {
    e.preventDefault();
    li.classList.remove("drop-target");
    const from = state.dragIndex;
    const to = Number(li.dataset.index);
    if (!from || from === to) return;

    // 目前的順序就是畫面上的順序；把被拖的那一項抽出來插到目標位置
    const order = state.status.items.map((i) => i.index);
    order.splice(order.indexOf(from), 1);
    order.splice(order.indexOf(to), 0, from);
    await refresh(await api(`/api/projects/${encodeURIComponent(state.name)}/reorder`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ order }),
    }));
  });
}

// ---------------------------------------------------------------------------
// 上傳

function pickFiles() { $("file-input").click(); }

async function upload(files) {
  if (!files || !files.length) return;
  const form = new FormData();
  [...files].forEach((f) => form.append("files", f));
  setStatus($("upload-status"), `上傳中… ${files.length} 個檔案`);
  try {
    const data = await api(`/api/projects/${encodeURIComponent(state.name)}/items`, {
      method: "POST", body: form,
    });
    let message = `已加入 ${data.added} 項`;
    if (data.skipped && data.skipped.length) {
      message += `，略過 ${data.skipped.length} 個重複的檔案`;
    }
    setStatus($("upload-status"), message + "。檢查中…");
    await refresh(data.status);
    await api(`/api/projects/${encodeURIComponent(state.name)}/check`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    }).then(refresh);
    setStatus($("upload-status"), message + "。");
  } catch (err) {
    setStatus($("upload-status"), err.message, true);
  }
}

async function replaceItem(index) {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = $("file-input").accept;
  input.addEventListener("change", async () => {
    if (!input.files.length) return;
    const form = new FormData();
    form.append("files", input.files[0]);
    setStatus($("upload-status"), `替換第 ${index} 項…`);
    try {
      // 只重跑這一項，其他項目的檢查結果保留
      await refresh(await api(
        `/api/projects/${encodeURIComponent(state.name)}/items/${index}/replace`,
        { method: "POST", body: form }));
      setStatus($("upload-status"), `第 ${index} 項已替換並重新檢查。`);
    } catch (err) {
      setStatus($("upload-status"), err.message, true);
    }
  });
  input.click();
}

async function removeItem(index) {
  if (!confirm(`確定要刪除第 ${index} 項嗎？後面的項目會往前遞補。`)) return;
  await refresh(await api(
    `/api/projects/${encodeURIComponent(state.name)}/items/${index}`, { method: "DELETE" }));
}

// ---------------------------------------------------------------------------
// 細節檢視

async function openDetail(item) {
  $("viewer-title").textContent = `第 ${item.index} 項　${item.original_name}`;
  const body = $("viewer-body");
  body.innerHTML = "";

  // **先把燈箱打開。** 以前 `hidden = false` 寫在函式最後一行，中間任何一步
  // 拋例外（例如讀記譜內容的 api() 在後端出錯時會 throw）燈箱就永遠不會顯示，
  // 使用者看到的是「按了『看細節』完全沒反應」，而且主控台以外沒有任何線索。
  $("viewer").hidden = false;

  if (item.issues.length) {
    const ul = document.createElement("ul");
    ul.className = "issue-list";
    item.issues.forEach((issue) => {
      const li = document.createElement("li");
      li.className = `issue ${issue.level}`;
      const b = document.createElement("b");
      b.textContent = issue.message;
      li.append(b);
      if (issue.hint) {
        const s = document.createElement("small");
        s.textContent = `→ ${issue.hint}`;
        li.append(s);
      }
      ul.append(li);
    });
    body.append(ul);
  }

  if (item.problems && item.problems.length) {
    const h = document.createElement("h4");
    h.textContent = "樂理檢查";
    const ul = document.createElement("ul");
    ul.className = "issue-list";
    item.problems.forEach((p) => {
      const li = document.createElement("li");
      li.className = `issue ${p.level === "error" ? "reject" : "warn"}`;
      const b = document.createElement("b");
      b.textContent = (p.measure ? `第 ${p.measure} 小節：` : "") + p.message;
      li.append(b);
      if (p.hint) {
        const s = document.createElement("small");
        s.textContent = `→ ${p.hint}`;
        li.append(s);
      }
      ul.append(li);
    });
    body.append(h, ul);
  }

  if (item.kind === "image") {
    const stem = item.file.replace(/\.[^.]+$/, "");
    // 兩張圖看的是不同階段的問題：標註圖是「拍得好不好」，
    // homr 的偵測圖是「有沒有切對譜表」。認錯的時候要分得出是哪一段出問題。
    [["拍攝品質檢查", `${stem}_check.jpg`],
     ["homr 偵測到的譜表與小節線", `${stem}_teaser.png`]].forEach(([label, file]) => {
      const wrap = document.createElement("div");
      const h = document.createElement("h4");
      h.textContent = label;
      const img = document.createElement("img");
      img.src = `/api/projects/${encodeURIComponent(state.name)}/raw/` +
        encodeURIComponent(file) + `?t=${Date.now()}`;
      img.alt = label;
      img.addEventListener("error", () => wrap.remove());
      wrap.append(h, img);
      body.append(wrap);
    });
  } else {
    // 讀不到內容不該讓整個燈箱空白 —— 上面的檢查結果與樂理問題還是有用的
    try {
      const data = await api(
        `/api/projects/${encodeURIComponent(state.name)}/text/${item.index}`);
      body.append(codeView(data.content, badLines(data.check)));
    } catch (err) {
      const p = document.createElement("p");
      p.className = "err";
      p.textContent = `讀不到記譜內容：${err.message}`;
      body.append(p);
    }
  }

  // 什麼都沒有的時候要講一聲，不然空白的燈箱看起來像壞掉
  if (!body.childNodes.length) {
    const p = document.createElement("p");
    p.textContent = "這一項目前沒有檢查結果 —— 先按「重新檢查全部」或「開始辨識」。";
    body.append(p);
  }
}

function badLines(check) {
  const lines = new Set();
  ((check && check.issues) || []).forEach((issue) => {
    const m = /第 (\d+) 行/.exec(issue.message || "");
    if (m) lines.add(Number(m[1]));
  });
  return lines;
}

function codeView(text, bad) {
  const pre = document.createElement("pre");
  pre.className = "code-view";
  text.replace(/\n$/, "").split("\n").forEach((line, i) => {
    const row = document.createElement("span");
    if (bad.has(i + 1)) row.className = "bad-line";
    const ln = document.createElement("span");
    ln.className = "ln";
    ln.textContent = String(i + 1).padStart(3, " ");
    row.append(ln, document.createTextNode(line + "\n"));
    pre.append(row);
  });
  return pre;
}

// ---------------------------------------------------------------------------
// 辨識

async function startBuild() {
  const body = {
    bpm: $("bpm").value ? Number($("bpm").value) : null,
    force: $("force").checked,
  };
  $("btn-build").disabled = true;
  $("build-log").hidden = false;
  $("build-log").textContent = "";
  $("build-result").innerHTML = "";
  $("build-result").dataset.live = "1";
  // 先把進度條擺出來。等第一次輪詢（1.2 秒後）才出現的話，
  // 按下去到有反應之間會有一段什麼都沒發生的空窗。
  setProgress({ state: "running", percent: 0, stage: "準備中" });
  try {
    await api(`/api/projects/${encodeURIComponent(state.name)}/build`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    pollBuild();
  } catch (err) {
    setStatus($("build-stage"), err.message, true);
    $("build-warning").hidden = true;
    $("btn-build").disabled = false;
  }
}

/* 進度條。後端的百分比是用階段表估的（web/app.py 的 _STAGE_START），
   只保證單調遞增 —— 所以旁邊一定要同時顯示階段名稱，
   光一個停在 30% 不動的數字沒辦法讓人判斷是在跑還是卡住了。 */
function setProgress(job) {
  const box = $("build-progress");
  if (!job || job.state === "idle") { box.hidden = true; return; }

  const pct = Math.max(0, Math.min(100, Math.round(Number(job.percent) || 0)));
  box.hidden = false;
  box.classList.toggle("running", job.state === "running");
  $("progress-fill").style.width = `${pct}%`;
  $("progress-percent").textContent = `${pct}%`;
  $("progress-stage").textContent =
    job.state === "error" ? "停在這一步：" + (job.stage || "") : (job.stage || "");
}

function pollBuild(once) {
  clearInterval(state.polling);
  const tick = async () => {
    let job;
    try {
      job = await api(`/api/projects/${encodeURIComponent(state.name)}/build/progress`);
    } catch { return; }

    if (job.state === "idle") { setProgress(job); $("btn-build").disabled = false; return; }

    setProgress(job);
    $("build-log").hidden = job.log.length === 0;
    $("build-log").textContent = job.log.map((l) => l.kind === "stage"
      ? `\n── ${l.text} ──` : `  ${l.text}`).join("\n").trim();
    $("build-log").scrollTop = $("build-log").scrollHeight;

    if (job.state === "running") {
      setStatus($("build-stage"), `辨識中… ${job.stage}`);
      // 關掉視窗會讓辨識白跑（一頁 15–40 秒，整首可能好幾分鐘），
      // 所以這句不能只是狀態列裡的一段小灰字 —— 要一直擺在眼前。
      const warn = $("build-warning");
      warn.hidden = false;
      warn.innerHTML =
        `<b>辨識中，請不要關掉這個視窗或分頁。</b>` +
        `<span>照片一頁大約 15–40 秒。關掉的話這次辨識就白跑了，要從頭再來。</span>`;
      $("btn-build").disabled = true;
      return;
    }

    clearInterval(state.polling);
    $("build-warning").hidden = true;
    $("btn-build").disabled = false;
    if (job.state === "error") {
      setStatus($("build-stage"), "沒有完成", true);
      showError(job.error);
    } else {
      setStatus($("build-stage"), "完成");
      showBuild(job.result.build, job.result);
    }
    await refresh();
  };

  tick();
  if (!once) state.polling = setInterval(tick, 1200);
}

function showError(message) {
  const card = document.createElement("div");
  card.className = "notice notice-bad";
  const body = document.createElement("div");
  body.className = "notice-body";

  const h = document.createElement("b");
  h.textContent = "沒有產出樂譜";
  const pre = document.createElement("pre");
  pre.textContent = message;

  // 錯誤內容常常是一整段 traceback，用選取的很容易漏掉開頭或結尾
  const actions = document.createElement("div");
  actions.className = "card-actions";
  const copy = button("複製錯誤內容", async () => {
    try {
      await navigator.clipboard.writeText(message);
      copy.textContent = "已複製";
      setTimeout(() => { copy.textContent = "複製錯誤內容"; }, 1500);
    } catch {
      copy.textContent = "複製失敗，請自己選取";
    }
  });
  actions.append(copy);

  body.append(h, pre, actions);
  card.append(body);
  $("build-result").innerHTML = "";
  $("build-result").append(card);
}

function showBuild(build, result) {
  const box = $("build-result");
  box.innerHTML = "";
  const card = document.createElement("div");
  card.className = "result-card";

  const h = document.createElement("h3");
  h.textContent = "樂譜已產出";
  const p = document.createElement("div");
  p.textContent = `${build.measures} 小節 / ${build.notes} 音符　信心 ${build.confidence}`;
  const t = document.createElement("div");
  t.textContent = `速度：${build.tempo_text || build.bpm + " BPM"}`;
  card.append(h, p, t);

  // 譜上找不到速度標記時就問 —— 悄悄用一個猜的值，
  // 音遊譜面會整個對不上而使用者不知道為什麼
  if (build.needs_bpm) card.append(bpmPrompt(build));

  if (result && result.report_text) {
    const t = document.createElement("pre");
    t.textContent = "樂理檢查發現的問題：\n" + result.report_text;
    card.append(t);
  }
  if (result && result.failed && result.failed.length) {
    const t = document.createElement("pre");
    t.textContent = "以下項目沒有成功，最終樂譜不含這些內容：\n" +
      result.failed.map((f) => `  第 ${f.index} 項 ${f.file}：${f.error}`).join("\n");
    card.append(t);
  }

  const downloads = document.createElement("div");
  downloads.className = "downloads";
  const base = `/api/projects/${encodeURIComponent(state.name)}/download`;
  downloads.append(link(`${base}/musicxml`, "下載 MusicXML（給評分用）"));
  (build.levels || []).forEach((level) => {
    downloads.append(link(`${base}/chart${level}`, `下載音遊譜面　難度 ${level}`));
  });
  card.append(downloads);

  const cmd = document.createElement("pre");
  // 難度用這份譜真的有的。分手失敗的譜只有難度 2，寫死 --level 1 的話
  // 使用者照著複製貼上會直接撞上「這份譜在難度 1 之下一個音符都不剩」。
  const lowest = (build.levels || [])[0] || 2;
  cmd.textContent = `拿去評分：\n  run.py play -s "${build.musicxml}" --level ${lowest}`;
  card.append(cmd);

  box.append(card);
}

function bpmPrompt(build) {
  const box = document.createElement("div");
  box.className = "bpm-ask";

  const msg = document.createElement("div");
  msg.textContent =
    `譜上找不到速度標記（節拍器記號或 Moderato 這類術語），` +
    `先用 ${build.bpm} BPM 產出音遊譜面 —— 音符落下的時間點會不準。`;

  const row = document.createElement("div");
  row.className = "bpm-row";
  const input = document.createElement("input");
  input.type = "number";
  input.min = 30;
  input.max = 300;
  input.placeholder = String(build.bpm);
  const apply = document.createElement("button");
  apply.className = "primary";
  apply.textContent = "套用這個速度";
  const note = document.createElement("span");
  note.className = "counts";
  note.textContent = "知道正確速度就填進去；不知道可以先跳過，之後再改。";

  const send = async () => {
    const value = Number(input.value);
    if (!value) return;
    apply.disabled = true;
    try {
      await refresh(await api(`/api/projects/${encodeURIComponent(state.name)}/bpm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bpm: value }),
      }));
      $("build-result").dataset.live = "";
      showBuild(state.status.build, null);
      if (typeof loadPreview === "function") loadPreview();
    } catch (err) {
      note.textContent = err.message;
      apply.disabled = false;
    }
  };
  apply.addEventListener("click", send);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });

  row.append(input, apply);
  box.append(msg, row, note);
  return box;
}

function link(href, text) {
  const a = document.createElement("a");
  a.href = href;
  a.textContent = text;
  return a;
}

// ---------------------------------------------------------------------------
// 新增專案

async function openNew() {
  $("newdlg").hidden = false;
  $("new-name").value = "";
  $("new-error").textContent = "";
  $("new-name").focus();
  if (!state.help) state.help = await api("/api/notation-help");
  renderHelp();
}

function renderHelp() {
  const type = document.querySelector("input[name=stype]:checked").value;
  const box = $("notation-help");
  box.innerHTML = "";
  if (type === "photo") {
    box.innerHTML =
      "<p>上傳每一頁的照片，系統會先檢查拍攝品質（模糊、歪斜、反光、解析度），" +
      "再用 AI 辨識成樂譜。拍的時候整頁入鏡、正對著拍、光線平均，成功率最高。</p>";
    return;
  }
  const help = state.help[type];
  const heading = document.createElement("h4");
  heading.textContent = `${help.title} 範例`;
  const example = document.createElement("pre");
  example.textContent = help.example;
  const table = document.createElement("table");
  [...help.pitch, ...state.help.common].forEach(([code, desc]) => {
    const tr = document.createElement("tr");
    const td1 = document.createElement("td");
    td1.textContent = code;
    const td2 = document.createElement("td");
    td2.textContent = desc;
    tr.append(td1, td2);
    table.append(tr);
  });
  box.append(heading, example, table);
}

async function createProject() {
  const name = $("new-name").value.trim();
  const source_type = document.querySelector("input[name=stype]:checked").value;
  try {
    await api("/api/projects", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, source_type }),
    });
    $("newdlg").hidden = true;
    await loadProjects(name);
  } catch (err) {
    $("new-error").textContent = err.message;
  }
}

// ---------------------------------------------------------------------------

function init() {
  $("project-select").addEventListener("change", (e) => {
    // 選回空的佔位項就是「回到什麼都沒開」的狀態
    if (!e.target.value) { loadProjects(); return; }
    $("app").hidden = false;
    $("no-project").hidden = true;
    $("btn-delete").disabled = false;
    selectProject(e.target.value);
  });
  $("btn-new").addEventListener("click", openNew);
  $("welcome-new").addEventListener("click", openNew);
  $("newdlg-close").addEventListener("click", () => { $("newdlg").hidden = true; });

  updateThemeButton();
  $("theme-toggle").addEventListener("click",
    () => applyTheme(currentTheme() === "dark" ? "light" : "dark"));
  // 沒有手動選過時，系統換主題按鈕的圖示也要跟著換
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", updateThemeButton);
  $("new-create").addEventListener("click", createProject);
  $("new-name").addEventListener("keydown", (e) => { if (e.key === "Enter") createProject(); });
  document.querySelectorAll("input[name=stype]").forEach(
    (r) => r.addEventListener("change", renderHelp));

  $("btn-delete").addEventListener("click", async () => {
    if (!confirm(`確定要刪除專案「${state.name}」嗎？上傳的檔案都會一起刪掉。`)) return;
    await api(`/api/projects/${encodeURIComponent(state.name)}`, { method: "DELETE" });
    await loadProjects();
  });

  const dz = $("dropzone");
  dz.addEventListener("click", pickFiles);
  dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") pickFiles(); });
  ["dragenter", "dragover"].forEach((type) => dz.addEventListener(type, (e) => {
    e.preventDefault(); dz.classList.add("over");
  }));
  ["dragleave", "drop"].forEach((type) => dz.addEventListener(type, (e) => {
    e.preventDefault(); dz.classList.remove("over");
  }));
  dz.addEventListener("drop", (e) => upload(e.dataTransfer.files));
  $("file-input").addEventListener("change", (e) => {
    upload(e.target.files);
    e.target.value = "";
  });

  $("btn-check").addEventListener("click", async () => {
    setStatus($("upload-status"), "檢查中…");
    await refresh(await api(`/api/projects/${encodeURIComponent(state.name)}/check`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ force: true }),
    }));
    setStatus($("upload-status"), "檢查完成。");
  });

  // 重新整理後瀏覽器會把上次輸入的數字還原回這個欄位（Firefox 連
  // autocomplete="off" 都照還原），而**還原的值會被送出**，於是靜靜蓋掉
  // AI 從譜上讀到的速度 —— 使用者只會看到欄位裡莫名其妙有個數字。
  // 速度的設計是「讀不到才問人」，所以每次載入都清空。
  $("bpm").value = "";

  $("btn-build").addEventListener("click", startBuild);
  $("viewer-close").addEventListener("click", () => { $("viewer").hidden = true; });
  $("viewer").addEventListener("click", (e) => {
    if (e.target === $("viewer")) $("viewer").hidden = true;
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    $("viewer").hidden = true;
    $("newdlg").hidden = true;
  });

  loadProjects();
}

init();
