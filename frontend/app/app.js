/* ================= AI 防护 · 移动端 App 前端 ================= */
(function () {
  "use strict";

  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));

  // ---------------- 运行环境 ----------------
  const IS_APP = location.hostname === "localhost" && !location.port; // Capacitor 壳内
  const store = {
    get api() {
      return localStorage.getItem("burn_api") ||
        (IS_APP && window.__DEFAULT_API__ ? window.__DEFAULT_API__ : "");
    },
    set api(v) { localStorage.setItem("burn_api", v); },
    get token() { return localStorage.getItem("burn_token") || ""; },
    set token(v) { localStorage.setItem("burn_token", v); },
  };

  function api(path) {
    return (store.api ? store.api.replace(/\/+$/, "") : "") + path;
  }

  function assetUrl(p) {
    return p && p.startsWith("/") ? api(p) : p;
  }

  // ---------------- 状态 ----------------
  const state = {
    file: null,
    dataUrl: "",
    meta: { site: "", area_hand: "", age_group: "", cause: "", red_flags: [] },
    caseId: null,
    pollTimer: null,
    tickets: [],
    ticketFilter: "pending",
    openTicket: null,
  };

  // ---------------- 通用 ----------------
  let toastTimer = null;
  function toast(msg, ms) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.add("on");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("on"), ms || 2400);
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function ago(ts) {
    const d = Math.floor(Date.now() / 1000 - ts);
    if (d < 60) return "刚刚";
    if (d < 3600) return Math.floor(d / 60) + " 分钟前";
    if (d < 86400) return Math.floor(d / 3600) + " 小时前";
    return new Date(ts * 1000).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
  }

  function fmtDate(ts) {
    return new Date(ts * 1000).toLocaleString("zh-CN", {
      month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  }

  // ---------------- 导航 ----------------
  const TITLES = {
    scan: ["AI 防护", "家庭热液烫伤 AI 判读"],
    history: ["我的记录", "本机保存的判读历史"],
    doctor: ["医生复核台", "低置信度转人工工单"],
    me: ["设置", "服务地址与模型信息"],
  };

  function go(tab) {
    $$(".view").forEach(v => (v.hidden = true));
    const map = { scan: "#v-scan", history: "#v-history", doctor: "#v-doctor", me: "#v-me" };
    $(map[tab]).hidden = false;
    $$(".tab").forEach(b => b.classList.toggle("on", b.dataset.tab === tab));
    const t = TITLES[tab];
    $("#barTitle").textContent = t[0];
    $("#barSub").textContent = t[1];
    window.scrollTo({ top: 0, behavior: "instant" });
    if (tab === "history") renderHistory();
    if (tab === "doctor") loadTickets();
    if (tab === "me") loadModelInfo();
  }

  $$(".tab").forEach(b => b.addEventListener("click", () => go(b.dataset.tab)));

  // ---------------- 服务状态 ----------------
  async function ping() {
    const dot = $("#netDot");
    try {
      const r = await fetch(api("/api/health"), { cache: "no-store" });
      const d = await r.json();
      dot.classList.toggle("on", !!d.model_ready);
      return d;
    } catch (e) {
      dot.classList.remove("on");
      return null;
    }
  }
  ping();
  setInterval(ping, 30000);

  $("#btnOnline").addEventListener("click", async () => {
    const d = await ping();
    toast(d ? (d.model_ready ? "服务正常 · 转人工阈值 " + Math.round(d.review_threshold * 100) + "%" : "服务在线但模型未就绪")
             : "连不上服务，请到「我的」检查服务地址");
  });

  // ---------------- 红旗 ----------------
  const FLAGS = [
    ["breath", "呼吸困难 / 声音嘶哑 / 烟灰痰", 2],
    ["unconscious", "意识不清、面色苍白、四肢湿冷", 2],
    ["large_area", "面积超过 10 个手掌（儿童 5 个）", 2],
    ["deep_dark", "创面焦黑 / 发白如皮革 / 触之无痛", 2],
    ["chemical", "化学灼伤或电击伤", 2],
    ["stuck_cloth", "衣物与创面粘连无法分离", 1],
    ["chronic", "糖尿病、免疫抑制、心脏病等基础病", 1],
  ];

  function renderFlags() {
    $("#flagList").innerHTML = FLAGS.map(([k, t, lv]) =>
      `<label class="flag" data-k="${k}"><span class="box"></span>
        <span>${esc(t)}${lv === 2 ? ' <b class="lv2">· 紧急</b>' : ""}</span></label>`).join("");
    $$("#flagList .flag").forEach(el => {
      el.addEventListener("click", e => {
        e.preventDefault();
        const k = el.dataset.k;
        const i = state.meta.red_flags.indexOf(k);
        if (i >= 0) state.meta.red_flags.splice(i, 1);
        else state.meta.red_flags.push(k);
        el.classList.toggle("on", i < 0);
      });
    });
  }
  renderFlags();

  // ---------------- 选项芯片 ----------------
  $$(".chips").forEach(box => {
    const key = box.dataset.key;
    $$("button", box).forEach(b => {
      b.addEventListener("click", () => {
        const on = b.classList.contains("on");
        $$("button", box).forEach(x => x.classList.remove("on"));
        if (!on) {
          b.classList.add("on");
          state.meta[key] = b.dataset.v;
        } else {
          state.meta[key] = "";
        }
      });
    });
  });

  // ---------------- 选图 ----------------
  const fileInput = $("#fileInput");

  function pick(camera) {
    if (camera) fileInput.setAttribute("capture", "environment");
    else fileInput.removeAttribute("capture");
    fileInput.value = "";
    fileInput.click();
  }
  $("#btnCamera").addEventListener("click", () => pick(true));
  $("#btnAlbum").addEventListener("click", () => pick(false));

  fileInput.addEventListener("change", () => {
    const f = fileInput.files && fileInput.files[0];
    if (!f) return;
    if (!/^image\//.test(f.type)) return toast("请选择图片文件");
    if (f.size > 12 * 1024 * 1024) return toast("图片超过 12MB，请压缩后重试");
    state.file = f;
    const fr = new FileReader();
    fr.onload = () => {
      state.dataUrl = fr.result;
      $("#previewImg").src = fr.result;
      $("#previewCard").hidden = false;
      $("#btnAnalyze").disabled = false;
      $("#previewCard").scrollIntoView({ behavior: "smooth", block: "center" });
    };
    fr.readAsDataURL(f);
  });

  $("#btnRetake").addEventListener("click", () => pick(true));

  // ---------------- 上传判读 ----------------
  $("#btnAnalyze").addEventListener("click", analyze);

  async function analyze() {
    if (!state.file) return toast("请先拍照或选择照片");
    if (!store.api && IS_APP) {
      toast("请先在「我的」里设置服务地址");
      return go("me");
    }
    $("#v-scan").hidden = true;
    $("#v-result").hidden = false;
    $("#resultBody").innerHTML = `<div class="loading"><div class="spin"></div>
      <p>AI 正在分析创面…</p><small>图像质量筛查 → 三头级联推理 → 温度校准</small></div>`;

    const fd = new FormData();
    fd.append("image", state.file, "wound.jpg");
    fd.append("meta", JSON.stringify(state.meta));
    fd.append("device_id", deviceId());

    const t0 = Date.now();
    try {
      const r = await fetch(api("/api/analyze"), { method: "POST", body: fd });
      const d = await r.json();
      if (!d.ok) throw new Error(d.error || "判读失败");
      const el = Math.round((Date.now() - t0) / 1000);
      state.caseId = d.case_id;
      renderResult(d, state.dataUrl, el);
      saveHistory(d, state.dataUrl);
      if (d.status === "pending_review") startPoll(d.case_id);
    } catch (e) {
      $("#resultBody").innerHTML = `<div class="card"><div class="banner danger">
        <span>⚠️</span><div><b>判读失败</b><br>${esc(e.message || e)}</div></div>
        <button class="btn ghost block" onclick="location.reload()">重试</button></div>`;
    }
  }

  function deviceId() {
    let d = localStorage.getItem("burn_device");
    if (!d) {
      d = "app-" + Math.random().toString(36).slice(2, 10);
      localStorage.setItem("burn_device", d);
    }
    return d;
  }

  // ---------------- 结果渲染 ----------------
  const CLASS_COLORS = ["#16a34a", "#ea9a12", "#dc2626"];
  const CLASS_SHORT = ["浅度烧伤", "深二度烧伤", "三度烧伤"];

  function ringSvg(conf, color) {
    const R = 36, C = 2 * Math.PI * R, off = C * (1 - conf);
    return `<svg viewBox="0 0 88 88" width="88" height="88">
      <circle cx="44" cy="44" r="${R}" fill="none" stroke="rgba(255,255,255,.25)" stroke-width="7"/>
      <circle cx="44" cy="44" r="${R}" fill="none" stroke="#fff" stroke-width="7"
        stroke-linecap="round" stroke-dasharray="${C}" stroke-dashoffset="${off}"
        style="transition:stroke-dashoffset 1s cubic-bezier(.2,.9,.2,1)"/>
    </svg>`;
  }

  function renderResult(d, imgUrl, secs) {
    const ai = d.ai, adv = d.advice;
    const cls = ai.class_index;
    const color = CLASS_COLORS[cls];
    const grading = cls === 0
      ? "linear-gradient(140deg,#15803d,#22c55e 60%,#4ade80)"
      : cls === 1
        ? "linear-gradient(140deg,#b45309,#ea9a12 60%,#fbbf24)"
        : "linear-gradient(140deg,#991b1b,#dc2626 60%,#f87171)";
    const names = ["浅度烧伤", "深二度烧伤", "三度烧伤"];

    let html = `
      <div class="res-card" style="background:${grading}">
        <div class="res-top">
          <div class="ring">${ringSvg(ai.confidence, color)}
            <div class="ring-txt">${Math.round(ai.confidence * 100)}%</div></div>
          <div style="min-width:0">
            <div class="res-name">${esc(CLASS_SHORT[cls])}</div>
            <div class="res-sub">${esc(ai.class_label)}</div>
            <span class="res-tag">${ai.need_review ? "低置信度 · 已转人工" : "AI 判读完成"} · ${secs}s</span>
          </div>
        </div>
        <div class="bars">
          ${names.map((n, i) => `<div class="bar-row"><span class="nm">${n}</span>
            <span class="bar-track"><span class="bar-fill" style="width:0"></span></span>
            <span class="pc">${Math.round(ai.probs[i] * 100)}%</span></div>`).join("")}
        </div>
      </div>`;

    // 转人工说明
    if (ai.need_review) {
      html += `<div class="banner warn" id="reviewBanner"><span>🩺</span><div>
        <b>已自动转烧伤科医生复核</b><br>
        ${ai.review_reasons.map(r => esc(r.text)).join("<br>")}
        <div style="margin-top:6px;opacity:.75" id="reviewWait">工单已推送，正在等待医生处理…</div>
      </div></div>`;
    } else {
      html += `<div class="banner ok"><span>✅</span><div>
        置信度 ${Math.round(ai.confidence * 100)}% 达到自动放行标准（阈值 ${Math.round(ai.threshold * 100)}%），无需转人工。</div></div>`;
    }

    // 质量提示
    if (ai.quality && ai.quality.score < 0.55) {
      html += `<div class="banner warn"><span>📷</span><div>
        <b>照片质量偏低（${Math.round(ai.quality.score * 100)} 分）</b><br>
        ${esc((ai.quality.flags || []).join("；") || "光线或对焦不足")}，建议重拍以提高准确率。</div></div>`;
    }

    // 分级建议
    const lvColor = adv.color;
    html += `<div class="card">
      <div class="adv-head">
        <span class="adv-lv" style="background:${lvColor}">${esc(adv.label)}</span>
        <span class="adv-eta">${esc(adv.eta)}</span>
      </div>
      <div style="font-size:14px;font-weight:600;line-height:1.5;margin-bottom:12px">
        ${esc(adv.headline)}</div>
      ${adv.escalated ? `<div class="banner danger" style="margin-top:0"><span>⬆️</span><div>
        <b>已安全升级</b>：因 ${adv.escalation_reasons.map(e => esc(e.text)).join("、")}</div></div>` : ""}
      <div class="section-t">现在这样做</div>
      <ol class="steps">${adv.actions.slice(0, 5).map(a => `<li>${esc(a)}</li>`).join("")}</ol>
      <div class="section-t">千万不要</div>
      <div class="avoid">${adv.avoid.slice(0, 6).map(a => `<span>${esc(a)}</span>`).join("")}</div>
      <div class="section-t">需要立即就医的信号</div>
      <ul class="steps" style="counter-reset:none">${adv.watch.slice(0, 4).map(w => `<li style="padding-left:20px">${esc(w)}</li>`).join("")}</ul>
    </div>`;

    // 溯源信息
    html += `<div class="card">
      <div class="card-h"><b>判读溯源</b></div>
      <div class="kv">
        <span class="k">病例编号</span><span class="v">${esc(d.case_id)}</span>
        <span class="k">温度参数 T</span><span class="v">${Number((ai.temperature && ai.temperature.head3) || 1).toFixed(2)}</span>
        <span class="k">原始置信度</span><span class="v">${Math.round(ai.raw_confidence * 100)}%</span>
        <span class="k">推理耗时</span><span class="v">${ai.latency_ms} ms</span>
      </div>
      <div class="section-t">原始照片</div>
      <img src="${imgUrl}" style="width:100%;border-radius:14px" alt="创面">
    </div>
    <p class="foot-note">${esc(adv.disclaimer)}</p>`;

    $("#resultBody").innerHTML = html;

    // 概率条动画
    requestAnimationFrame(() => {
      $$("#resultBody .bar-fill").forEach((el, i) => {
        setTimeout(() => (el.style.width = ai.probs[i] * 100 + "%"), 120 + i * 90);
      });
      $$("#resultBody .ring circle")[1].style.strokeDashoffset = "0";
    });
  }

  // ---------------- 轮询复核结果 ----------------
  function applyReviewed(d) {
    const rv = (d.reviews || []).slice(-1)[0];
    const banner = $("#reviewBanner");
    if (banner) {
      banner.className = "banner ok";
      banner.innerHTML = `<span>✅</span><div><b>医生复核完成</b><br>
        复核结论：${rv && rv.decision === "confirm" ? "确认 AI 判读" : "修正为「" + esc(d.case.class_name) + "」"}
        ${rv && rv.note ? "<br>医生意见：" + esc(rv.note) : ""}
        <br><span style="opacity:.8">处置建议已按复核结论更新。</span></div>`;
      banner.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    updateAdvice(d.advice);
  }

  function startPoll(cid) {
    clearInterval(state.pollTimer);
    state.pollTimer = setInterval(async () => {
      try {
        const r = await fetch(api("/api/case/" + cid));
        const d = await r.json();
        if (!d.ok) return;
        if (d.case.status === "reviewed") {
          clearInterval(state.pollTimer);
          applyReviewed(d);
        }
      } catch (e) { /* 忽略网络抖动 */ }
    }, 3000);
  }

  function updateAdvice(adv) {
    const el = $("#resultBody .adv-lv");
    if (!el) return;
    el.textContent = adv.label;
    el.style.background = adv.color;
    const eta = $("#resultBody .adv-eta");
    if (eta) eta.textContent = adv.eta;
    const ol = $("#resultBody .steps");
    if (ol) ol.innerHTML = adv.actions.slice(0, 5).map(a => `<li>${esc(a)}</li>`).join("");
  }

  $("#btnBackScan").addEventListener("click", () => {
    clearInterval(state.pollTimer);
    $("#v-result").hidden = true;
    $("#v-scan").hidden = false;
    go("scan");
  });

  // ---------------- 历史记录 ----------------
  function saveHistory(d, thumb) {
    const list = JSON.parse(localStorage.getItem("burn_history") || "[]");
    list.unshift({
      case_id: d.case_id, ts: Math.floor(Date.now() / 1000),
      cls: d.ai.class_index, conf: d.ai.confidence,
      status: d.status, label: d.advice.label, thumb,
    });
    localStorage.setItem("burn_history", JSON.stringify(list.slice(0, 50)));
  }

  function renderHistory() {
    const list = JSON.parse(localStorage.getItem("burn_history") || "[]");
    const box = $("#historyList");
    if (!list.length) {
      box.innerHTML = `<div class="empty"><span class="e-ico">🗂</span>
        还没有判读记录<br>回到「判读」拍一张创面照片开始</div>`;
      return;
    }
    box.innerHTML = list.map(h => `
      <div class="hitem" data-id="${esc(h.case_id)}">
        <img src="${h.thumb || "/app/icons/icon-192.png"}" alt="">
        <div class="hi-b">
          <div class="hi-t">${esc(CLASS_SHORT[h.cls] || "")}
            <span class="pill ${h.status === "reviewed" ? "done" : h.status === "auto" ? "done" : "pend"}">
              ${h.status === "pending_review" ? "待复核" : h.status === "reviewed" ? "已复核" : "已放行"}</span>
          </div>
          <div class="hi-s">${fmtDate(h.ts)} · 置信度 ${Math.round(h.conf * 100)}% · ${esc(h.label || "")}</div>
        </div>
      </div>`).join("");
    $$("#historyList .hitem").forEach(el => {
      el.addEventListener("click", () => openCase(el.dataset.id));
    });
  }

  async function openCase(cid) {
    $("#v-result").hidden = false;
    $("#v-scan").hidden = true;
    $("#resultBody").innerHTML = `<div class="loading"><div class="spin"></div><p>载入病例…</p></div>`;
    try {
      const r = await fetch(api("/api/case/" + cid));
      const d = await r.json();
      if (!d.ok) throw new Error("病例不存在");
      const fake = {
        case_id: d.case.id,
        ai: {
          class_index: d.case.ai_class, class_label: d.case.class_label,
          class_name: d.case.class_name, confidence: d.case.ai_confidence,
          probs: d.case.ai_probs, raw_confidence: d.case.ai_confidence,
          need_review: !!(d.case.review_reasons && d.case.review_reasons.length),
          review_reasons: d.case.review_reasons || [],
          threshold: d.threshold, quality: { score: d.case.quality_score, flags: d.case.quality_flags || [] },
          temperature: { head3: d.case.temperature || 1 }, latency_ms: 0,
        },
        status: d.case.status, advice: d.advice,
      };
      renderResult(fake, assetUrl(d.case.image_url), "—");
      if (d.case.status === "pending_review" || d.case.status === "in_review") startPoll(cid);
      else if (d.case.status === "reviewed") setTimeout(() => applyReviewed(d), 60);
    } catch (e) {
      $("#resultBody").innerHTML = `<div class="card"><div class="banner danger">
        <span>⚠️</span><div>${esc(e.message)}</div></div></div>`;
    }
  }

  // ---------------- 医生端 ----------------
  $("#btnLogin").addEventListener("click", async () => {
    const u = $("#docUser").value.trim();
    const p = $("#docPass").value;
    $("#loginErr").textContent = "";
    try {
      const r = await fetch(api("/api/doctor/login"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: u, password: p }),
      });
      const d = await r.json();
      if (!d.ok) throw new Error(d.error || "登录失败");
      store.token = d.token;
      $("#docLogin").hidden = true;
      $("#docList").hidden = false;
      toast("欢迎，" + d.doctor.name);
      loadTickets();
    } catch (e) {
      $("#loginErr").textContent = e.message;
    }
  });

  $$(".tabs-mini button").forEach(b => {
    b.addEventListener("click", () => {
      $$(".tabs-mini button").forEach(x => x.classList.remove("on"));
      b.classList.add("on");
      state.ticketFilter = b.dataset.st;
      loadTickets();
    });
  });

  async function loadTickets() {
    if (!store.token) return;
    try {
      const st = state.ticketFilter === "all" ? "" : "&status=" + state.ticketFilter;
      const r = await fetch(api("/api/doctor/tickets?_=1" + st), { headers: { "X-Token": store.token } });
      if (r.status === 401) { store.token = ""; $("#docList").hidden = true; $("#docLogin").hidden = false; return; }
      const d = await r.json();
      state.tickets = d.tickets || [];
      // 统计
      const all = await (await fetch(api("/api/doctor/stats"), { headers: { "X-Token": store.token } })).json();
      const s = all.stats || {};
      $("#docStats").innerHTML = `
        <div class="stat"><b>${s.pending ?? 0}</b><span>待复核</span></div>
        <div class="stat"><b>${s.reviewed_today ?? 0}</b><span>今日已复核</span></div>
        <div class="stat"><b>${s.review_rate != null ? Math.round(s.review_rate * 100) + "%" : "—"}</b><span>转人工率</span></div>`;
      renderTickets();
      $("#docList").hidden = false;
      $("#docLogin").hidden = true;
    } catch (e) {
      toast("工单加载失败：" + e.message);
    }
  }

  function renderTickets() {
    const box = $("#ticketList");
    if (!state.tickets.length) {
      box.innerHTML = `<div class="empty"><span class="e-ico">✅</span>当前没有${state.ticketFilter === "pending" ? "待处理的" : ""}工单</div>`;
      return;
    }
    box.innerHTML = state.tickets.map(t => `
      <div class="tk ${t.priority === "high" ? "high" : "normal"}" data-id="${esc(t.id)}">
        <div class="tk-t">
          <span class="badge ${t.priority === "high" ? "h" : "n"}">${t.priority === "high" ? "紧急" : "普通"}</span>
          AI：${esc(t.ai_class_name)} · ${Math.round(t.ai_confidence * 100)}%
          ${t.status === "done" ? '<span class="pill done" style="margin-left:auto">已完成</span>' : ""}
        </div>
        <div class="tk-s">${esc(t.id)} · ${ago(t.created_at)}${t.overdue ? ' · <b style="color:var(--danger)">已超时</b>' : ""}</div>
        ${(t.review_reasons || []).length ? `<div class="tk-r">${esc(t.review_reasons[0].text || "")}</div>` : ""}
      </div>`).join("");
    $$("#ticketList .tk").forEach(el => el.addEventListener("click", () => openTicket(el.dataset.id)));
  }

  async function openTicket(tid) {
    const t = state.tickets.find(x => x.id === tid);
    if (!t) return;
    state.openTicket = t;
    $("#docList").hidden = true;
    $("#docDetail").hidden = false;
    $("#docDetail").innerHTML = `<div class="loading"><div class="spin"></div><p>载入工单…</p></div>`;
    let c = null;
    try {
      const r = await fetch(api("/api/case/" + t.case_id));
      c = await r.json();
    } catch (e) { /* ignore */ }

    const needClaim = t.status === "pending";
    $("#docDetail").innerHTML = `
      <button class="back" id="btnBackList">‹ 返回工单列表</button>
      <img class="det-img" src="${assetUrl(t.image_url)}" alt="创面">
      <div class="card">
        <div class="card-h"><b>AI 判读</b>
          <span class="hint">${Math.round(t.ai_confidence * 100)}% · 阈值 ${c ? Math.round(c.threshold * 100) : "—"}%</span></div>
        <div class="kv">
          <span class="k">AI 结论</span><span class="v">${esc(t.ai_class_name)}</span>
          <span class="k">优先级</span><span class="v">${t.priority === "high" ? "紧急 · 30 分钟" : "普通 · 4 小时"}</span>
          <span class="k">提交时间</span><span class="v">${fmtDate(t.created_at)}</span>
          <span class="k">患者信息</span><span class="v">${patientText(c && c.case.user_meta)}</span>
        </div>
        ${(t.review_reasons || []).length ? `<div class="banner warn" style="margin-top:12px">
          <span>🚩</span><div>${t.review_reasons.map(r => esc(r.text)).join("<br>")}</div></div>` : ""}
        ${c && c.advice ? `<div class="banner" style="background:#f4f7fa;color:var(--ink-2)">
          <span>📋</span><div>AI 建议分级：<b>${esc(c.advice.label)}</b><br>${esc(c.advice.short)}</div></div>` : ""}
      </div>
      <div class="card" id="rvCard">
        <div class="card-h"><b>复核结论</b></div>
        <div class="radio-row" id="rvOpts">
          <label class="radio on" data-v="confirm"><span class="r-ico"></span>确认 AI 判读（${esc(t.ai_class_name)}）</label>
          <label class="radio" data-v="correct0"><span class="r-ico"></span>修正为：浅度烧伤</label>
          <label class="radio" data-v="correct1"><span class="r-ico"></span>修正为：深二度烧伤</label>
          <label class="radio" data-v="correct2"><span class="r-ico"></span>修正为：三度烧伤</label>
        </div>
        <div class="field" style="margin-top:12px">
          <label>复核意见（可选）</label>
          <textarea id="rvNote" placeholder="例：基底苍白潮湿，触痛迟钝，考虑深二度"></textarea>
        </div>
        ${needClaim ? `<button class="cta small" id="btnClaim">领取工单</button>` : ""}
        <button class="cta small" id="btnSubmit" ${needClaim ? "disabled style='opacity:.45'" : ""}>提交复核</button>
      </div>`;

    $("#btnBackList").addEventListener("click", () => {
      $("#docDetail").hidden = true;
      $("#docList").hidden = false;
    });

    let decision = "confirm", corrected = null;
    $$("#rvOpts .radio").forEach(el => {
      el.addEventListener("click", () => {
        $$("#rvOpts .radio").forEach(x => x.classList.remove("on"));
        el.classList.add("on");
        const v = el.dataset.v;
        if (v === "confirm") { decision = "confirm"; corrected = null; }
        else { decision = "correct"; corrected = parseInt(v.slice(-1), 10); }
      });
    });

    if (needClaim) {
      $("#btnClaim").addEventListener("click", async () => {
        try {
          const r = await fetch(api(`/api/doctor/tickets/${t.id}/claim`), {
            method: "POST", headers: { "X-Token": store.token, "Content-Type": "application/json" },
            body: "{}",
          });
          const d = await r.json();
          if (!d.ok) throw new Error(d.error || "领取失败");
          $("#btnClaim").remove();
          const sb = $("#btnSubmit");
          sb.disabled = false; sb.style.opacity = "1";
          toast("已领取，请在时限内完成复核");
        } catch (e) { toast(e.message); }
      });
    }

    $("#btnSubmit").addEventListener("click", async () => {
      try {
        const r = await fetch(api(`/api/doctor/tickets/${t.id}/review`), {
          method: "POST", headers: { "X-Token": store.token, "Content-Type": "application/json" },
          body: JSON.stringify({
            decision, corrected_class: corrected, note: $("#rvNote").value.trim(),
          }),
        });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || "提交失败");
        toast("复核完成，已回传用户端");
        $("#docDetail").hidden = true;
        $("#docList").hidden = false;
        loadTickets();
      } catch (e) { toast(e.message); }
    });
  }

  function patientText(meta) {
    if (!meta) return "—";
    const site = { hand_foot: "手/足", arm_leg: "四肢", trunk: "躯干", face_neck: "面颈", joint: "关节", perineum: "会阴" };
    const age = { adult: "成人", child: "儿童", elder: "老人", infant: "婴幼儿" };
    const cause = { hot_liquid: "热水热汤", steam: "蒸汽", oil: "热油", other: "其他" };
    return [age[meta.age_group], site[meta.site], meta.area_hand ? meta.area_hand + " 掌" : "", cause[meta.cause]]
      .filter(Boolean).join(" · ") || "—";
  }

  // ---------------- 设置 ----------------
  function normBase(v) {
    v = (v || "").trim().replace(/\/+$/, "");
    if (!v) return "";
    if (!/^https?:\/\//i.test(v)) v = "http://" + v;
    return v;
  }

  async function loadModelInfo() {
    $("#apiBase").value = store.api || (IS_APP ? "" : location.origin);
    const box = $("#meModel");
    box.innerHTML = `<span class="k">连接中…</span><span class="v"></span>`;
    try {
      const r = await fetch(api("/api/model"));
      const d = await r.json();
      const c = d.card || {};
      const ece = c.ece && (c.ece.after != null ? c.ece.after : null);
      box.innerHTML = `
        <span class="k">模型架构</span><span class="v">ResNet50 · 三头级联</span>
        <span class="k">温度校准</span><span class="v">${c.calibrated ? "已校准" : "未校准"} · T=${(c.temperature && c.temperature.head3) || 1}</span>
        <span class="k">ECE</span><span class="v">${ece != null ? (ece * 100).toFixed(1) + "%" : "—"}</span>
        <span class="k">转人工阈值</span><span class="v">${Math.round((c.review_threshold || 0) * 100)}%</span>
        <span class="k">测试集准确率</span><span class="v">${c.metrics && c.metrics.accuracy != null ? (c.metrics.accuracy * 100).toFixed(1) + "%" : "—"}</span>`;
    } catch (e) {
      box.innerHTML = `<span class="k">服务未连接</span><span class="v">请检查地址</span>`;
    }
  }

  $("#btnSaveApi").addEventListener("click", async () => {
    const v = normBase($("#apiBase").value);
    store.api = v;
    $("#apiMsg").textContent = "";
    const d = await ping();
    if (d) { $("#apiMsg").style.color = "var(--ok)"; $("#apiMsg").textContent = "已连接 · 模型" + (d.model_ready ? "就绪" : "未就绪"); }
    else { $("#apiMsg").style.color = "var(--danger)"; $("#apiMsg").textContent = "保存了，但连不上该地址"; }
    loadModelInfo();
  });

  $("#btnTestApi").addEventListener("click", async () => {
    const v = normBase($("#apiBase").value);
    const old = store.api;
    store.api = v;
    const d = await ping();
    store.api = old;
    $("#apiMsg").style.color = d ? "var(--ok)" : "var(--danger)";
    $("#apiMsg").textContent = d ? `连接成功 · 阈值 ${Math.round(d.review_threshold * 100)}%` : "连接失败，检查 IP 与防火墙";
  });

  // ---------------- 启动 ----------------
  if (IS_APP && !store.api) {
    setTimeout(() => {
      go("me");
      toast("首次使用：请填写电脑的局域网地址", 3200);
    }, 400);
  }
  if (store.token) {
    $("#docLogin").hidden = true;
    $("#docList").hidden = false;
  }

  // 快捷方式入口：?tab=doctor / ?action=camera
  const qs = new URLSearchParams(location.search);
  if (qs.get("tab") === "doctor") go("doctor");
  if (qs.get("action") === "camera") setTimeout(() => pick(true), 500);

  // Service Worker（安全上下文：https 或 localhost）
  const secure = location.protocol === "https:" ||
    ["localhost", "127.0.0.1"].includes(location.hostname);
  if ("serviceWorker" in navigator && secure) {
    navigator.serviceWorker.register("/app/sw.js", { scope: "/app/" }).catch(() => {});
  }
})();
