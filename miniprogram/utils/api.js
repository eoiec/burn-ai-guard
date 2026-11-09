const { BASE_URL, SHARE_CODE } = require("../config");

function withShare(path) {
  if (!SHARE_CODE || /[?&]sharecode=/.test(path)) return path;
  return path + (path.includes("?") ? "&" : "?") + "sharecode=" + SHARE_CODE;
}

function request(path, options = {}) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: BASE_URL + withShare(path),
      method: options.method || "GET",
      data: options.data || {},
      header: Object.assign({ "Content-Type": "application/json" }, options.header || {}),
      success: res => (res.data && res.data.ok ? resolve(res.data) : reject(res.data || res)),
      fail: reject
    });
  });
}

function upload(path, filePath, formData) {
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: BASE_URL + withShare(path),
      filePath,
      name: "image",
      formData: formData || {},
      success: res => {
        try {
          const d = JSON.parse(res.data);
          d.ok ? resolve(d) : reject(d);
        } catch (e) { reject(e); }
      },
      fail: reject
    });
  });
}

function device() {
  let d = wx.getStorageSync("burn_device");
  if (!d) { d = "MP" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6); wx.setStorageSync("burn_device", d); }
  return d;
}

module.exports = { request, upload, device };
