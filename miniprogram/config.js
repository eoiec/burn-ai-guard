// 服务地址：真机预览请改成局域网 IP，如 http://192.168.1.5:5000
module.exports = {
  BASE_URL: "http://127.0.0.1:5000",
  CLASS_NAMES: ["浅度烧伤", "深二度烧伤", "三度烧伤"],
  CLASS_LABELS: ["浅度烧伤（Ⅰ度+浅Ⅱ度）", "深二度烧伤（深Ⅱ度）", "三度烧伤（Ⅲ度）"],
  CLASS_COLOR: ["#0e9f6e", "#d97706", "#dc2626"],
  RED_FLAGS: [
    { k: "breath", t: "呼吸困难 / 声音嘶哑" },
    { k: "unconscious", t: "意识不清 / 面色苍白" },
    { k: "large_area", t: "大面积（成人>10 手掌）" },
    { k: "deep_dark", t: "创面焦黑 / 触之无痛" },
    { k: "chemical", t: "化学灼伤 / 电击" },
    { k: "stuck_cloth", t: "衣物与创面粘连" },
    { k: "chronic", t: "糖尿病等基础疾病" }
  ]
};
