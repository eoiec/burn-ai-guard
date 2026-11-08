const api = require("../../utils/api");
const { CLASS_LABELS, CLASS_COLOR, RED_FLAGS } = require("../../config");

Page({
  data: {
    step: 1,
    imgPath: "",
    flags: RED_FLAGS.map(f => ({ ...f, on: false })),
    ages: [{ k: "adult", t: "成人（5–69岁）" }, { k: "infant", t: "婴幼儿（<5岁）" }, { k: "elder", t: "老年（≥70岁）" }],
    sites: [{ k: "trunk", t: "躯干" }, { k: "limb", t: "四肢" }, { k: "hand_foot", t: "手/足" },
      { k: "face_neck", t: "面部/颈部" }, { k: "perineum", t: "会阴" }, { k: "joint", t: "关节" }],
    areas: ["<1 手掌", "1–5 手掌", "5–10 手掌", ">10 手掌"],
    causes: [{ k: "hot_liquid", t: "热水/热汤" }, { k: "steam", t: "蒸汽" }, { k: "flame", t: "火焰" },
      { k: "chemical", t: "化学" }, { k: "electrical", t: "电击" }, { k: "other", t: "其他" }],
    ageIdx: 0, siteIdx: 0, areaIdx: 0, causeIdx: 0,
    analyzing: false,
    result: null, advice: null, probsPct: [], confPct: 0, confColor: "#0f766e",
    reviewStatus: "", reviewDone: null,
    urgent: false
  },

  chooseImage() {
    wx.chooseMedia({
      count: 1, mediaType: ["image"], sourceType: ["album", "camera"], sizeType: ["compressed"],
      success: res => this.setData({ imgPath: res.tempFiles[0].tempFilePath, step: 2, result: null, reviewDone: null })
    });
  },

  pickAge(e) { this.setData({ ageIdx: +e.detail.value }); },
  pickSite(e) { this.setData({ siteIdx: +e.detail.value }); },
  pickArea(e) { this.setData({ areaIdx: +e.detail.value }); },
  pickCause(e) { this.setData({ causeIdx: +e.detail.value }); },
  toggleFlag(e) {
    const i = e.currentTarget.dataset.i;
    this.setData({ [`flags[${i}].on`]: !this.data.flags[i].on });
  },

  analyze() {
    if (this.data.analyzing) return;
    const d = this.data;
    const meta = {
      age_group: d.ages[d.ageIdx].k, site: d.sites[d.siteIdx].k,
      area_hand: String(d.areaIdx), cause: d.causes[d.causeIdx].k,
      red_flags: d.flags.filter(f => f.on).map(f => f.k)
    };
    this.setData({ analyzing: true, step: 3 });
    api.upload("/api/analyze", d.imgPath, {
      meta: JSON.stringify(meta), device_id: api.device()
    }).then(res => {
      const ai = res.ai;
      this.setData({
        analyzing: false, step: 4, result: res, advice: res.advice,
        confPct: Math.round(ai.confidence * 1000) / 10,
        confColor: CLASS_COLOR[ai.class_index],
        probsPct: ai.probs.map((p, i) => ({
          name: ["浅度烧伤", "深二度烧伤", "三度烧伤"][i],
          pct: Math.round(p * 1000) / 10, color: CLASS_COLOR[i]
        })),
        urgent: ai.class_index === 2 || res.advice.level === 2,
        reviewStatus: ai.need_review ? "pending" : "none"
      });
      if (ai.need_review) this.poll();
    }).catch(err => {
      this.setData({ analyzing: false, step: 2 });
      wx.showToast({ title: (err && err.error) || "分析失败", icon: "none" });
    });
  },

  poll() {
    if (this._timer) clearTimeout(this._timer);
    this._timer = setTimeout(async () => {
      try {
        const d = await api.request("/api/case/" + this.data.result.case_id);
        if (d.case.status === "reviewed") {
          const rv = d.reviews[d.reviews.length - 1];
          const cs = d.case;
          this.setData({
            reviewStatus: "done", reviewDone: rv,
            advice: d.advice,
            label: cs.class_label,
            urgent: cs.final_class === 2 || d.advice.level === 2
          });
        } else this.poll();
      } catch (e) { /* 网络波动静默重试 */ }
    }, 5000);
  },

  restart() { this.setData({ step: 1, imgPath: "", result: null, reviewStatus: "" }); },
  onUnload() { if (this._timer) clearTimeout(this._timer); }
});
