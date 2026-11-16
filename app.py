import os, re, secrets, sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv
load_dotenv()
from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__, template_folder="templates", static_folder="static")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(
    SECRET_KEY=os.environ.get("FLASK_SECRET_KEY", ""),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "true").lower() == "true",
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
)
if not app.config["SECRET_KEY"]:
    raise RuntimeError("Set FLASK_SECRET_KEY in your environment before starting the app.")

CLIENT_ID=os.environ.get("DISCORD_CLIENT_ID", "")
CLIENT_SECRET=os.environ.get("DISCORD_CLIENT_SECRET", "")
REDIRECT_URI=os.environ.get("DISCORD_REDIRECT_URI", "")
ADMIN_IDS={x.strip() for x in os.environ.get("ADMIN_DISCORD_IDS", "").split(",") if x.strip()}
DATABASE=os.environ.get("DATABASE_PATH", "apex.sqlite3")
DISCORD_API="https://discord.com/api/v10"
CATEGORIES={"Roleplay", "Gaming", "Community", "Creator", "Other"}


def db():
    conn=sqlite3.connect(DATABASE)
    conn.row_factory=sqlite3.Row
    return conn


def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS users (
            discord_id TEXT PRIMARY KEY, username TEXT NOT NULL, avatar TEXT,
            created_at TEXT NOT NULL, last_seen TEXT NOT NULL)""")
        con.execute("""CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id TEXT NOT NULL,
            server_name TEXT NOT NULL, category TEXT NOT NULL, description TEXT NOT NULL,
            invite_url TEXT NOT NULL, invite_code TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending', rejection_reason TEXT,
            created_at TEXT NOT NULL, reviewed_at TEXT, reviewed_by TEXT)""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_submissions_owner ON submissions(owner_id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_submissions_status ON submissions(status)")

init_db()


def now_iso(): return datetime.now(timezone.utc).isoformat()


def current_user():
    uid=session.get("discord_id")
    if not uid: return None
    with db() as con:
        row=con.execute("SELECT * FROM users WHERE discord_id=?", (uid,)).fetchone()
    return dict(row) if row else None

@app.context_processor
def inject_globals():
    return {"current_user": current_user(), "is_admin": session.get("discord_id") in ADMIN_IDS}


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("discord_id"):
            flash("Sign in with Discord to continue.", "error")
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapped


def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapped(*args, **kwargs):
        if session.get("discord_id") not in ADMIN_IDS: abort(403)
        return fn(*args, **kwargs)
    return wrapped


def csrf_token():
    if "csrf_token" not in session: session["csrf_token"]=secrets.token_urlsafe(32)
    return session["csrf_token"]
app.jinja_env.globals["csrf_token"] = csrf_token

@app.before_request
def protect_posts():
    if request.method == "POST":
        supplied=request.form.get("csrf_token", "")
        if not supplied or not secrets.compare_digest(supplied, session.get("csrf_token", "")):
            abort(400, "Invalid form security token. Refresh and try again.")

@app.get("/")
def home():
    with db() as con:
        listings=con.execute("SELECT * FROM submissions WHERE status='approved' ORDER BY reviewed_at DESC LIMIT 60").fetchall()
    return render_template("index.html", listings=listings)

@app.get("/terms")
def terms(): return render_template("terms.html")
@app.get("/privacy")
def privacy(): return render_template("privacy.html")

@app.get("/login")
def login():
    if not CLIENT_ID or not CLIENT_SECRET or not REDIRECT_URI:
        return render_template("setup_error.html"), 503
    state=secrets.token_urlsafe(32); session["oauth_state"]=state
    session["oauth_next"]=request.args.get("next", "")
    params={"client_id":CLIENT_ID,"redirect_uri":REDIRECT_URI,"response_type":"code","scope":"identify","state":state}
    return redirect("https://discord.com/oauth2/authorize?"+urlencode(params))

@app.get("/auth/callback")
def auth_callback():
    error=request.args.get("error")
    if error:
        flash("Discord sign-in was cancelled or could not be completed.", "error"); return redirect(url_for("home"))
    state=request.args.get("state", "")
    expected=session.pop("oauth_state", "")
    if not expected or not secrets.compare_digest(state, expected): abort(400, "Invalid OAuth state.")
    code=request.args.get("code")
    if not code: abort(400, "Discord did not return an authorization code.")
    token_response=requests.post("https://discord.com/api/oauth2/token", data={
        "client_id":CLIENT_ID,"client_secret":CLIENT_SECRET,"grant_type":"authorization_code",
        "code":code,"redirect_uri":REDIRECT_URI
    }, headers={"Content-Type":"application/x-www-form-urlencoded"}, timeout=15)
    if not token_response.ok: abort(502, "Discord token exchange failed. Check your OAuth redirect URI and credentials.")
    access_token=token_response.json().get("access_token")
    if not access_token:
    app.logger.error("Discord OAuth returned no access token.")
    abort(502, "Discord did not provide an access token.")

try:
    profile_response = requests.get(
        DISCORD_API + "/users/@me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20
    )

    if not profile_response.ok:
        app.logger.error(
            "Discord profile request failed: HTTP %s - %s",
            profile_response.status_code,
            profile_response.text[:500]
        )
        abort(502, "Discord profile verification failed. Please try again.")

    profile = profile_response.json()

except requests.RequestException:
    app.logger.exception("Could not connect to Discord API.")
    abort(502, "Could not connect to Discord. Please try again.")
    next_target=session.pop("oauth_next", "")
    profile=profile_response.json(); uid=str(profile["id"])
    avatar=profile.get("avatar")
    avatar_url=f"https://cdn.discordapp.com/avatars/{uid}/{avatar}.png?size=128" if avatar else "https://cdn.discordapp.com/embed/avatars/0.png"
    username=profile.get("global_name") or profile.get("username") or "Discord member"
    stamp=now_iso()
    with db() as con:
        con.execute("INSERT INTO users(discord_id,username,avatar,created_at,last_seen) VALUES(?,?,?,?,?) ON CONFLICT(discord_id) DO UPDATE SET username=excluded.username, avatar=excluded.avatar, last_seen=excluded.last_seen",(uid,username,avatar_url,stamp,stamp))
    session.clear(); session["discord_id"]=uid; session.permanent=True
    flash(f"Welcome to Apex, {username}.", "success")
    return redirect(next_target if next_target and next_target.startswith("/") and not next_target.startswith("//") else url_for("dashboard"))

@app.post("/logout")
@login_required
def logout(): session.clear(); flash("You have been signed out.", "success"); return redirect(url_for("home"))

@app.route("/dashboard", methods=["GET"])
@login_required
def dashboard():
    with db() as con:
        rows=con.execute("SELECT * FROM submissions WHERE owner_id=? ORDER BY created_at DESC",(session["discord_id"],)).fetchall()
    return render_template("dashboard.html", submissions=rows)

@app.route("/submit", methods=["GET","POST"])
@login_required
def submit():
    if request.method == "POST":
        name=request.form.get("server_name", "").strip()
        category=request.form.get("category", "")
        description=request.form.get("description", "").strip()
        invite=request.form.get("invite_url", "").strip()
        match=re.fullmatch(r"https?://(?:www\.)?(?:discord\.gg/|discord\.com/invite/)([A-Za-z0-9-]{2,})/?", invite, re.I)
        if not (2 <= len(name) <= 60): flash("Server name must be between 2 and 60 characters.", "error")
        elif category not in CATEGORIES: flash("Choose a valid category.", "error")
        elif not (20 <= len(description) <= 500): flash("Description must be between 20 and 500 characters.", "error")
        elif not match: flash("Enter a valid Discord invite link (discord.gg or discord.com/invite).", "error")
        else:
            code=match.group(1); stamp=now_iso()
            with db() as con:
                previous=con.execute("SELECT created_at FROM submissions WHERE invite_code=? ORDER BY created_at DESC LIMIT 1",(code,)).fetchone()
                if previous and datetime.fromisoformat(previous["created_at"]) > datetime.now(timezone.utc)-timedelta(hours=24):
                    flash("This server was submitted within the last 24 hours. Please wait before submitting it again.", "error")
                else:
                    try:
                        con.execute("INSERT INTO submissions(owner_id,server_name,category,description,invite_url,invite_code,status,created_at) VALUES(?,?,?,?,?,?, 'pending', ?)",(session["discord_id"],name,category,description,invite,code,stamp))
                        flash("Your server has been submitted for staff review.", "success"); return redirect(url_for("dashboard"))
                    except sqlite3.IntegrityError:
                        flash("That invite is already listed. Contact staff if you need to update it.", "error")
    return render_template("submit.html", categories=sorted(CATEGORIES))

@app.get("/admin")
@admin_required
def admin():
    with db() as con:
        submissions=con.execute("SELECT s.*,u.username FROM submissions s LEFT JOIN users u ON u.discord_id=s.owner_id ORDER BY CASE s.status WHEN 'pending' THEN 0 ELSE 1 END, s.created_at DESC").fetchall()
        stats={"users":con.execute("SELECT COUNT(*) FROM users").fetchone()[0],"pending":con.execute("SELECT COUNT(*) FROM submissions WHERE status='pending'").fetchone()[0],"approved":con.execute("SELECT COUNT(*) FROM submissions WHERE status='approved'").fetchone()[0]}
    return render_template("admin.html", submissions=submissions, stats=stats)

@app.post("/admin/submissions/<int:submission_id>/<action>")
@admin_required
def review_submission(submission_id, action):
    if action not in {"approve","reject"}: abort(404)
    reason=request.form.get("reason", "").strip()[:300]
    with db() as con:
        row=con.execute("SELECT id FROM submissions WHERE id=?",(submission_id,)).fetchone()
        if not row: abort(404)
        status="approved" if action=="approve" else "rejected"
        con.execute("UPDATE submissions SET status=?, rejection_reason=?, reviewed_at=?, reviewed_by=? WHERE id=?",(status,reason if action=="reject" else None,now_iso(),session["discord_id"],submission_id))
    flash(f"Submission #{submission_id} {status}.", "success"); return redirect(url_for("admin"))

@app.get("/health")
def health(): return {"status":"ok","service":"apex-advertising"}

@app.errorhandler(403)
def forbidden(_): return render_template("message.html", title="Access denied", message="This page is restricted to authorized Apex administrators."),403

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)

@app.context_processor
def inject_year(): return {"current_year": datetime.now(timezone.utc).year}
