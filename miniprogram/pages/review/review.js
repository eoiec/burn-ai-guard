const api = require("../../utils/api");
const { CLASS_NAMES } = require("../../config");

Page({
  data: {
    t: null, claimed: false, done: false, finalText: "",
    decision: "confirm", correctIdx: null,
    CLASS: CLASS_NAMES
  },

  onLoad(q) {
    this.tid = q.tid;
    this.load();
  },

  tokenHeader() { return { "X-Token": wx.getStorageSync("burn_token") }; },

  load() {
    api.request("/api/doctor/tickets", { header: this.tokenHeader() })
      .then(d => {
        const t = d.tickets.find(x => x.id === this.tid);
        if (!t) { wx.showToast({ title: "工单不存在", icon: "none" }); return; }
        t.conf_pct = (t.ai_confidence * 100).toFixed(1);
        t.probs = t.ai_probs.map((p, i) => ({
          name: CLASS_NAMES[i], pct: (p * 100).toFixed(1),
          color: ["#0e9f6e", "#d97706", "#dc2626"][i]
        }));
        t.reasons = (t.review_reasons || []).map(r => r.text);
        t.finalName = t.final_class != null ? CLASS_NAMES[t.final_class] : "";
        this.setData({ t, claimed: t.status === "claimed", done: t.status === "done" });
      })
      .catch(err => this._authFail(err));
  },

  _authFail() { wx.removeStorageSync("burn_token"); wx.navigateBack(); },

  pickDecision(e) { this.setData({ decision: e.currentTarget.dataset.d, correctIdx: null }); },
  pickClass(e) { this.setData({ correctIdx: +e.currentTarget.dataset.c }); },
  inputNote(e) { this.note = e.detail.value; },

  claim() {
    api.request(`/api/doctor/tickets/${this.tid}/claim`, { method: "POST", header: this.tokenHeader() })
      .then(() => this.load())
      .catch(err => wx.showToast({ title: (err && err.error) || "领取失败", icon: "none" }));
  },

  submit() {
    const d = this.data;
    if (d.decision === "correct" && d.correctIdx === null) {
      wx.showToast({ title: "请选择修正后的类别", icon: "none" }); return;
    }
    api.request(`/api/doctor/tickets/${this.tid}/review`, {
      method: "POST", header: this.tokenHeader(),
      data: {
        decision: d.decision,
        corrected_class: d.decision === "correct" ? d.correctIdx : null,
        note: this.note || ""
      }
    }).then(res => {
      wx.showToast({ title: "已回传用户端", icon: "success" });
      this.load();
    }).catch(err => wx.showToast({ title: (err && err.error) || "提交失败", icon: "none" }));
  }
});
