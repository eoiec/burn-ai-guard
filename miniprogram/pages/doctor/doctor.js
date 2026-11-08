const api = require("../../utils/api");

Page({
  data: {
    logged: false, doctor: null,
    user: "doctor", pass: "burn2026",
    stats: null, tickets: [], filter: "pending"
  },

  onShow() { if (this.data.logged) this.refresh(); },

  inputUser(e) { this.setData({ user: e.detail.value }); },
  inputPass(e) { this.setData({ pass: e.detail.value }); },

  login() {
    api.request("/api/doctor/login", { method: "POST", data: { username: this.data.user, password: this.data.pass } })
      .then(d => {
        wx.setStorageSync("burn_token", d.token);
        this.setData({ logged: true, doctor: d.doctor });
        this.refresh();
      })
      .catch(err => wx.showToast({ title: (err && err.error) || "登录失败", icon: "none" }));
  },

  logout() { wx.removeStorageSync("burn_token"); this.setData({ logged: false, tickets: [], stats: null }); },

  tokenHeader() { return { "X-Token": wx.getStorageSync("burn_token") }; },

  pickFilter(e) { this.setData({ filter: e.detail.value }); this.refresh(); },
  refresh() { this.loadStats(); this.loadTickets(); },

  loadStats() {
    api.request("/api/doctor/stats", { header: this.tokenHeader() })
      .then(d => {
        const s = d.stats;
        s.referral_pct = (s.referral_rate * 100).toFixed(1);
        s.revision_pct = (s.revision_rate * 100).toFixed(1);
        s.avg_min = s.avg_review_seconds ? Math.round(s.avg_review_seconds / 60) : "—";
        this.setData({ stats: s });
      })
      .catch(err => this._authFail(err));
  },

  loadTickets() {
    api.request("/api/doctor/tickets?status=" + this.data.filter, { header: this.tokenHeader() })
      .then(d => {
        const now = Date.now() / 1000;
        const tickets = d.tickets.map(t => ({
          ...t,
          conf_pct: (t.ai_confidence * 100).toFixed(1),
          prob_text: t.ai_probs.map((p, i) =>
            ["浅度", "深二度", "三度"][i] + " " + (p * 100).toFixed(0) + "%").join(" · "),
          wait_text: now - t.created_at > 90 ? Math.round((now - t.created_at) / 60) + " 分钟" : Math.round(now - t.created_at) + " 秒",
          status_name: { pending: "待领取", claimed: "复核中", done: "已完成" }[t.status]
        }));
        this.setData({ tickets });
      })
      .catch(err => this._authFail(err));
  },

  _authFail() { wx.removeStorageSync("burn_token"); this.setData({ logged: false }); },

  openTicket(e) {
    wx.navigateTo({ url: "/pages/review/review?tid=" + e.currentTarget.dataset.id });
  }
});
