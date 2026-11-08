"""全局配置：路径、阈值、临床规则常量。"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA_DIR = os.path.join(BASE_DIR, "data")
MODEL_DIR = os.path.join(BASE_DIR, "models")
REPORT_DIR = os.path.join(BASE_DIR, "reports")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
DB_PATH = os.path.join(DATA_DIR, "burn_guard.sqlite3")

CHECKPOINT_PATH = os.path.join(MODEL_DIR, "burnnet_resnet50.pt")
CALIBRATION_PATH = os.path.join(MODEL_DIR, "calibration.json")

CLASS_KEYS = ["superficial", "deep_partial", "full"]
CLASS_NAMES = ["浅度烧伤", "深二度烧伤", "三度烧伤"]
CLASS_LABELS = ["浅度烧伤（Ⅰ度 + 浅Ⅱ度）", "深二度烧伤（深Ⅱ度）", "三度烧伤（Ⅲ度）"]
CLASS_URGENCY = ["居家处理", "尽快就医", "立即急救送医"]

# ---- 安全机制参数 -------------------------------------------------------
# 置信度低于该阈值 -> 强制转人工复核（阈值由 ml/calibrate.py 在验证集上敏感性分析后写入
# calibration.json，此处仅为缺省兜底值）
DEFAULT_REVIEW_THRESHOLD = 0.75

# 三度烧伤"风险信号"护栏：即使 top1 不是三度，只要三度概率超过该值就转人工，
# 避免"三度被错判为浅度"造成的漏诊（假阴性代价远高于转人工成本）。
FULL_THICKNESS_RISK_PROB = 0.20

# 图像质量红线：低于该分数视为不可判读，必须转人工并提示重拍
QUALITY_MIN = 0.35

# 待复核工单的响应时限（分钟），按优先级分级
SLA_MINUTES = {"high": 30, "normal": 240}

# 医生端登录口令（演示用；生产环境应接入医院 HIS/统一身份认证）
DOCTOR_ACCOUNTS = [
    {"username": "doctor", "password": os.environ.get("BURN_DOCTOR_PWD", "burn2026"),
     "name": "李医生", "title": "烧伤科 主治医师", "role": "doctor"},
    {"username": "admin", "password": os.environ.get("BURN_ADMIN_PWD", "admin2026"),
     "name": "系统管理员", "title": "项目管理", "role": "admin"},
]

MAX_UPLOAD_BYTES = 12 * 1024 * 1024
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic"}
