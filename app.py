import logging
import os
import secrets
import sqlite3
import time
import uuid
from collections import defaultdict, deque
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

try:
    from authlib.integrations.flask_client import OAuth
except Exception:
    OAuth = None

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / "Uploaded_Files"
MODEL_PATH = BASE_DIR / "model" / "df_model.pt"
DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "aisentinal.db")))

ALLOWED_VIDEO_EXTS = {"mp4", "mov", "avi", "mkv", "webm"}

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", "dev-change-this-secret"),
    MAX_CONTENT_LENGTH=int(os.getenv("MAX_UPLOAD_MB", "250")) * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "0") == "1",
    RATE_LIMIT_WINDOW_SEC=int(os.getenv("RATE_LIMIT_WINDOW_SEC", "60")),
    RATE_LIMIT_AUTH_MAX=int(os.getenv("RATE_LIMIT_AUTH_MAX", "10")),
    RATE_LIMIT_DETECT_MAX=int(os.getenv("RATE_LIMIT_DETECT_MAX", "5")),
)

UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("aisentinal")
APP_START_TIME = time.time()
_rate_limit_buckets = defaultdict(deque)

oauth = OAuth(app) if OAuth else None
google_oauth = None
if oauth and os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"):
    google_oauth = oauth.register(
        name="google",
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )

_model = None


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    cur = db.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            email TEXT UNIQUE,
            password_hash TEXT,
            full_name TEXT,
            auth_provider TEXT DEFAULT 'local',
            oauth_sub TEXT,
            is_admin INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cur.execute("PRAGMA table_info(users)")
    columns = {row[1] for row in cur.fetchall()}
    if "username" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN username TEXT")
    if "is_admin" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
    if "session_nonce" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN session_nonce TEXT")
    if "session_updated_at" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN session_updated_at TEXT")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            output TEXT,
            confidence REAL,
            notes TEXT,
            error TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    admin = cur.execute(
        "SELECT id FROM users WHERE username = ? OR email = ?", ("admin", "admin@local")
    ).fetchone()
    if not admin:
        cur.execute(
            """
            INSERT INTO users (username, email, password_hash, full_name, auth_provider, is_admin)
            VALUES (?, ?, ?, ?, 'local', 1)
            """,
            ("admin", "admin@local", generate_password_hash("admin"), "Administrator"),
        )
        logger.info("Seeded default admin user: admin / admin")

    db.commit()
    db.close()


def run_startup_checks():
    issues = []
    if app.config["SECRET_KEY"] == "dev-change-this-secret":
        issues.append("SECRET_KEY is using default value.")
    if not MODEL_PATH.exists():
        issues.append(f"Model file is missing: {MODEL_PATH}")
    if not UPLOAD_FOLDER.exists():
        issues.append(f"Upload folder is missing: {UPLOAD_FOLDER}")
    if not google_oauth:
        issues.append("Google OAuth is not configured (optional).")

    if issues:
        for issue in issues:
            logger.warning("Startup check: %s", issue)
    else:
        logger.info("Startup checks passed.")


def _allowed_video(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_VIDEO_EXTS


def _safe_next(target: str | None) -> str | None:
    if not target:
        return None
    if target.startswith("/") and not target.startswith("//"):
        return target
    return None


def _client_key():
    if g.user:
        return f"user:{g.user['id']}"
    return f"ip:{request.remote_addr or 'unknown'}"


def _is_rate_limited(bucket: str, key: str, limit: int, window_sec: int) -> bool:
    now = time.time()
    q = _rate_limit_buckets[(bucket, key)]
    while q and (now - q[0]) > window_sec:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


def _get_or_create_csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def _csrf_valid() -> bool:
    sent_token = request.form.get("csrf_token") or request.headers.get("X-CSRFToken")
    session_token = session.get("_csrf_token")
    return bool(sent_token and session_token and secrets.compare_digest(sent_token, session_token))


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user:
            return redirect(url_for("login", next=request.path))
        if not g.user["is_admin"]:
            flash("Admin access required.", "error")
            return redirect(url_for("dashboard_page"))
        return view(*args, **kwargs)

    return wrapped


@app.before_request
def load_current_user():
    g.user = None
    user_id = session.get("user_id")
    if user_id:
        g.user = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        session_nonce = session.get("session_nonce")
        # Enforce single active session per user by checking stored nonce.
        if g.user and (not session_nonce or g.user["session_nonce"] != session_nonce):
            session.clear()
            g.user = None
            if request.endpoint not in {
                "login",
                "login_confirm",
                "home",
                "about_page",
                "contact_page",
                "legacy_styles",
                "legacy_script",
                "favicon",
                "health",
                "static",
            }:
                return redirect(url_for("login"))

    # CSRF protection for all state-changing requests.
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if not _csrf_valid():
            abort(400, description="Invalid or missing CSRF token.")

    # Basic in-memory rate limiting for auth and detection endpoints.
    key = _client_key()
    window = app.config["RATE_LIMIT_WINDOW_SEC"]
    endpoint = request.endpoint or ""
    if request.method == "POST" and endpoint in {"login", "signup"}:
        if _is_rate_limited("auth", key, app.config["RATE_LIMIT_AUTH_MAX"], window):
            abort(429, description="Too many auth attempts. Please try again later.")
    if request.method == "POST" and endpoint == "detect_page":
        if _is_rate_limited("detect", key, app.config["RATE_LIMIT_DETECT_MAX"], window):
            abort(429, description="Too many detection uploads. Please try again later.")


@app.context_processor
def inject_globals():
    return {
        "current_user": g.user,
        "google_oauth_enabled": bool(google_oauth),
        "csrf_token": _get_or_create_csrf_token,
    }


def _get_detection_model():
    global _model
    if _model is not None:
        return _model

    import torch
    from torch import nn
    from torchvision import models

    class Model(nn.Module):
        def __init__(self, num_classes, latent_dim=2048, lstm_layers=1, hidden_dim=2048, bidirectional=False):
            super().__init__()
            model = models.resnext50_32x4d(weights="ResNeXt50_32X4D_Weights.IMAGENET1K_V1")
            self.model = nn.Sequential(*list(model.children())[:-2])
            self.lstm = nn.LSTM(latent_dim, hidden_dim, lstm_layers, bidirectional)
            self.dp = nn.Dropout(0.4)
            self.linear1 = nn.Linear(2048, num_classes)
            self.avgpool = nn.AdaptiveAvgPool2d(1)

        def forward(self, x):
            batch_size, seq_length, c, h, w = x.shape
            x = x.view(batch_size * seq_length, c, h, w)
            fmap = self.model(x)
            x = self.avgpool(fmap)
            x = x.view(batch_size, seq_length, 2048)
            x_lstm, _ = self.lstm(x, None)
            return fmap, self.dp(self.linear1(x_lstm[:, -1, :]))

    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Model file not found at {MODEL_PATH}")

    model = Model(2)
    model.load_state_dict(torch.load(MODEL_PATH, map_location=torch.device("cpu")))
    model.eval()
    _model = model
    return _model


def _run_deepfake_detection(video_path: Path):
    os.environ["KMP_DUPLICATE_LIB_OK"] = "True"

    import warnings

    warnings.filterwarnings("ignore")

    import cv2
    try:
        import face_recognition
    except Exception:
        face_recognition = None
    import torch
    from torch import nn
    from torch.utils.data import Dataset
    from torchvision import transforms

    class ValidationDataset(Dataset):
        def __init__(self, video_names, sequence_length=20, transform=None):
            self.video_names = video_names
            self.transform = transform
            self.count = sequence_length

        def __len__(self):
            return len(self.video_names)

        def __getitem__(self, idx):
            current_video_path = self.video_names[idx]
            frames = []
            for frame in self.frame_extract(current_video_path):
                if frame is None or getattr(frame, "size", 0) == 0:
                    continue

                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                faces = []
                if face_recognition is not None:
                    try:
                        faces = face_recognition.face_locations(rgb_frame)
                    except Exception:
                        faces = []

                if faces:
                    top, right, bottom, left = faces[0]
                    h, w = rgb_frame.shape[:2]
                    top = max(0, min(top, h))
                    bottom = max(0, min(bottom, h))
                    left = max(0, min(left, w))
                    right = max(0, min(right, w))
                    if bottom > top and right > left:
                        rgb_frame = rgb_frame[top:bottom, left:right, :]

                frames.append(self.transform(rgb_frame))
                if len(frames) == self.count:
                    break

            if not frames:
                raise RuntimeError("No readable video frames found.")

            while len(frames) < self.count:
                frames.append(frames[-1])

            stacked = torch.stack(frames[: self.count])
            return stacked.unsqueeze(0)

        @staticmethod
        def frame_extract(path):
            vid_obj = cv2.VideoCapture(str(path))
            success = True
            while success:
                success, image = vid_obj.read()
                if success:
                    yield image

    transform_pipeline = transforms.Compose(
        [
            transforms.ToPILImage(),
            transforms.Resize((112, 112)),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ]
    )

    dataset = ValidationDataset([str(video_path)], sequence_length=20, transform=transform_pipeline)
    model = _get_detection_model()

    softmax = nn.Softmax(dim=1)
    _, logits = model(dataset[0])
    probs = softmax(logits)
    _, pred_idx = torch.max(probs, 1)
    confidence = float(probs[:, int(pred_idx.item())].item() * 100)

    label = "REAL" if int(pred_idx.item()) == 1 else "FAKE"
    return label, round(confidence, 2)


@app.route("/")
@app.route("/index.html")
def home():
    if g.user:
        return redirect(url_for("app_home"))
    return render_template("index.html")


@app.route("/about.html")
def about_page():
    return render_template("about.html")


@app.route("/contact.html")
def contact_page():
    return render_template("contact.html")


@app.route("/signup", methods=["GET", "POST"])
@app.route("/signup.html", methods=["GET", "POST"])
def signup():
    if g.user:
        return redirect(url_for("app_home"))

    if request.method == "GET":
        return render_template("signup.html")

    username = request.form.get("username", "").strip().lower()
    full_name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    if not username or not email or not password:
        flash("Username, email, and password are required.", "error")
        return render_template("signup.html")
    if len(password) < 6:
        flash("Password must be at least 6 characters.", "error")
        return render_template("signup.html")

    db = get_db()
    existing = db.execute(
        "SELECT id FROM users WHERE lower(username) = ? OR lower(email) = ?", (username, email)
    ).fetchone()
    if existing:
        flash("Username or email already exists.", "error")
        return render_template("signup.html")

    db.execute(
        """
        INSERT INTO users (username, email, password_hash, full_name, auth_provider)
        VALUES (?, ?, ?, ?, 'local')
        """,
        (username, email, generate_password_hash(password), full_name),
    )
    db.commit()
    flash("Account created. Please login.", "success")
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
@app.route("/login.html", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("app_home"))

    if request.method == "GET":
        return render_template("login.html")

    identity = request.form.get("identity", "").strip().lower()
    password = request.form.get("password", "")

    user = get_db().execute(
        "SELECT * FROM users WHERE lower(username) = ? OR lower(email) = ?", (identity, identity)
    ).fetchone()

    if not user or not user["password_hash"] or not check_password_hash(user["password_hash"], password):
        flash("Invalid credentials.", "error")
        return render_template("login.html")

    # Existing active session found on another device/browser.
    if user["session_nonce"]:
        session["pending_login_user_id"] = user["id"]
        session["pending_login_next"] = _safe_next(request.args.get("next")) or url_for("app_home")
        return render_template(
            "login.html",
            login_conflict=True,
            conflict_identity=identity,
        )

    new_nonce = secrets.token_urlsafe(24)
    get_db().execute(
        "UPDATE users SET session_nonce = ?, session_updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (new_nonce, user["id"]),
    )
    get_db().commit()
    session.clear()
    session["user_id"] = user["id"]
    session["session_nonce"] = new_nonce
    flash("Logged in successfully.", "success")
    return redirect(_safe_next(request.args.get("next")) or url_for("app_home"))


@app.route("/login/confirm", methods=["POST"])
def login_confirm():
    user_id = session.get("pending_login_user_id")
    next_url = session.get("pending_login_next") or url_for("app_home")
    action = (request.form.get("action") or "").strip().lower()

    if not user_id:
        flash("Login confirmation has expired. Please login again.", "error")
        return redirect(url_for("login"))

    if action == "no":
        session.pop("pending_login_user_id", None)
        session.pop("pending_login_next", None)
        flash("Login cancelled. Existing session remains active.", "error")
        return redirect(url_for("login"))

    if action != "yes":
        flash("Invalid confirmation action.", "error")
        return redirect(url_for("login"))

    user = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        session.pop("pending_login_user_id", None)
        session.pop("pending_login_next", None)
        flash("User not found for login confirmation.", "error")
        return redirect(url_for("login"))

    new_nonce = secrets.token_urlsafe(24)
    get_db().execute(
        "UPDATE users SET session_nonce = ?, session_updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (new_nonce, user["id"]),
    )
    get_db().commit()

    session.clear()
    session["user_id"] = user["id"]
    session["session_nonce"] = new_nonce
    flash("Previous session ended. Logged in on this device.", "success")
    return redirect(next_url)


@app.route("/logout", methods=["POST"])
def logout():
    if g.user:
        db = get_db()
        db.execute("UPDATE users SET session_nonce = NULL, session_updated_at = CURRENT_TIMESTAMP WHERE id = ?", (g.user["id"],))
        db.commit()
    session.clear()
    flash("Logged out.", "success")
    return redirect(url_for("home"))


@app.route("/auth/google")
def auth_google():
    if not google_oauth:
        flash("Google OAuth is not configured.", "error")
        return redirect(url_for("login"))

    redirect_uri = url_for("auth_google_callback", _external=True)
    return google_oauth.authorize_redirect(redirect_uri)


@app.route("/auth/google/callback")
def auth_google_callback():
    if not google_oauth:
        flash("Google OAuth is not configured.", "error")
        return redirect(url_for("login"))

    token = google_oauth.authorize_access_token()
    user_info = token.get("userinfo") or google_oauth.parse_id_token(token)
    if not user_info:
        flash("Google login failed.", "error")
        return redirect(url_for("login"))

    email = (user_info.get("email") or "").strip().lower()
    sub = user_info.get("sub")
    full_name = user_info.get("name") or "Google User"

    if not email:
        flash("Google account email not available.", "error")
        return redirect(url_for("login"))

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE lower(email) = ?", (email,)).fetchone()
    if not user:
        base_username = email.split("@")[0]
        username = base_username
        idx = 1
        while db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
            idx += 1
            username = f"{base_username}{idx}"

        db.execute(
            """
            INSERT INTO users (username, email, password_hash, full_name, auth_provider, oauth_sub)
            VALUES (?, ?, NULL, ?, 'google', ?)
            """,
            (username, email, full_name, sub),
        )
        db.commit()
        user = db.execute("SELECT * FROM users WHERE lower(email) = ?", (email,)).fetchone()

    session.clear()
    session["user_id"] = user["id"]
    flash("Signed in with Google.", "success")
    return redirect(url_for("app_home"))


@app.route("/app.html")
@login_required
def app_home():
    return render_template("app.html")


@app.route("/dashboard.html")
@login_required
def dashboard_page():
    if g.user["is_admin"]:
        return redirect(url_for("admin_dashboard_page"))

    db = get_db()
    scans = db.execute(
        """
        SELECT filename, output, confidence, created_at
        FROM scans
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 10
        """,
        (g.user["id"],),
    ).fetchall()

    summary = db.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN output = 'FAKE' THEN 1 ELSE 0 END) AS flagged,
            SUM(CASE WHEN output = 'REAL' THEN 1 ELSE 0 END) AS authentic
        FROM scans
        WHERE user_id = ?
        """,
        (g.user["id"],),
    ).fetchone()

    return render_template("dashboard.html", scans=scans, summary=summary)


@app.route("/admin/dashboard.html")
@admin_required
def admin_dashboard_page():
    db = get_db()
    stats = db.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM users) AS total_users,
            (SELECT COUNT(*) FROM scans) AS total_uploads,
            (SELECT COUNT(*) FROM scans WHERE output IN ('REAL', 'FAKE')) AS total_detections,
            (SELECT COUNT(*) FROM scans WHERE output = 'FAKE') AS flagged_count,
            (SELECT COUNT(*) FROM scans WHERE output = 'REAL') AS authentic_count,
            (SELECT COUNT(*) FROM scans WHERE output = 'ERROR') AS error_count
        """
    ).fetchone()

    activities = db.execute(
        """
        SELECT
            s.id,
            u.username,
            u.email,
            s.filename,
            s.output,
            s.confidence,
            s.notes,
            s.error,
            s.created_at
        FROM scans s
        JOIN users u ON u.id = s.user_id
        ORDER BY s.id DESC
        LIMIT 100
        """
    ).fetchall()

    # Stage 03 ask: keep engagement/forwarding metrics static for now.
    engagement = {
        "total_clicks": 2431,
        "forwarded_items": 186,
        "report_downloads": 97,
        "api_calls": 652,
    }

    return render_template(
        "admin_dashboard.html",
        stats=stats,
        activities=activities,
        engagement=engagement,
    )


@app.route("/Detect", methods=["GET", "POST"])
@app.route("/detect", methods=["GET", "POST"])
@app.route("/detect.html", methods=["GET", "POST"])
@login_required
def detect_page():
    if request.method == "GET":
        return render_template("detect.html")

    uploaded = request.files.get("video")
    notes = (request.form.get("notes") or "").strip()

    if not uploaded or not uploaded.filename:
        return render_template("detect.html", error="Please choose a video file first.")

    filename = secure_filename(uploaded.filename)
    if not _allowed_video(filename):
        return render_template(
            "detect.html",
            error="Unsupported file type. Upload one of: mp4, mov, avi, mkv, webm.",
        )

    temp_name = f"{uuid.uuid4().hex}_{filename}"
    saved_path = UPLOAD_FOLDER / temp_name
    uploaded.save(saved_path)

    db = get_db()
    try:
        output, confidence = _run_deepfake_detection(saved_path)
        db.execute(
            """
            INSERT INTO scans (user_id, filename, output, confidence, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (g.user["id"], filename, output, confidence, notes),
        )
        db.commit()
        data = {"output": output, "confidence": confidence}
        return render_template("detect.html", data=data)
    except Exception as exc:
        logger.exception("Detection failed")
        db.execute(
            """
            INSERT INTO scans (user_id, filename, output, confidence, notes, error)
            VALUES (?, ?, 'ERROR', NULL, ?, ?)
            """,
            (g.user["id"], filename, notes, str(exc)),
        )
        db.commit()
        return render_template(
            "detect.html",
            error=f"Detection engine error: {exc}. Check dependencies/model configuration.",
        )
    finally:
        if saved_path.exists():
            saved_path.unlink()


@app.route("/detected.html")
@login_required
def detected_page():
    fake_scans = get_db().execute(
        """
        SELECT filename, confidence, created_at, notes
        FROM scans
        WHERE user_id = ? AND output = 'FAKE'
        ORDER BY id DESC
        LIMIT 30
        """,
        (g.user["id"],),
    ).fetchall()
    return render_template("detected.html", fake_scans=fake_scans)


@app.route("/faq.html")
@login_required
def faq_page():
    return render_template("faq.html")


@app.route("/health")
def health():
    db_ok = True
    try:
        get_db().execute("SELECT 1").fetchone()
    except Exception:
        db_ok = False
    return jsonify(
        {
            "status": "ok" if db_ok else "degraded",
            "database": "ok" if db_ok else "error",
            "model_present": MODEL_PATH.exists(),
            "uptime_sec": int(time.time() - APP_START_TIME),
        }
    ), (200 if db_ok else 503)


@app.errorhandler(400)
def bad_request(error):
    description = getattr(error, "description", "Bad request")
    endpoint = request.endpoint or ""
    if endpoint == "login":
        return render_template("login.html", error=description), 400
    if endpoint == "signup":
        return render_template("signup.html", error=description), 400
    if endpoint == "detect_page":
        return render_template("detect.html", error=description), 400
    return jsonify({"error": description}), 400


@app.errorhandler(413)
def payload_too_large(_error):
    return render_template("detect.html", error="Upload too large. Please use a smaller video file."), 413


@app.errorhandler(429)
def too_many_requests(error):
    description = getattr(error, "description", "Too many requests.")
    endpoint = request.endpoint or ""
    if endpoint == "login":
        return render_template("login.html", error=description), 429
    if endpoint == "signup":
        return render_template("signup.html", error=description), 429
    if endpoint == "detect_page":
        return render_template("detect.html", error=description), 429
    return jsonify({"error": description}), 429


@app.route("/styles.css")
def legacy_styles():
    return send_from_directory(app.static_folder, "styles.css")


@app.route("/script.js")
def legacy_script():
    return send_from_directory(app.static_folder, "script.js")


@app.route("/favicon.ico")
def favicon():
    return ("", 204)


@app.route("/<path:page>")
def unhandled_page(page: str):
    abort(404)


if __name__ == "__main__":
    init_db()
    run_startup_checks()
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "1") == "1",
    )
