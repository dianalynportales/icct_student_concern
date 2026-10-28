import base64
import csv
import hashlib
import io
import os
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, Response, jsonify, redirect, render_template, request, session
from sqlalchemy import create_engine, text

from logic import CATEGORIES, STATUSES, KeywordClassifier, make_responder, make_summary

try:
    from logos_data import LOGOS
except ImportError:
    LOGOS = {}

TEACHER_CODE = os.environ.get("TEACHER_CODE", "ICCT2026")
TAGLINE = "Your Concern Matters"

app = Flask(__name__, template_folder=".")
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret")

DB_URL = os.environ.get("DATABASE_URL", "")
if DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql://", 1)
if not DB_URL:
    # Local: file in project folder. On Vercel without a DB: /tmp (temporary only!)
    path = "/tmp/icct_concerns.db" if os.environ.get("VERCEL") else "icct_concerns.db"
    DB_URL = "sqlite:///" + path
IS_PG = DB_URL.startswith("postgresql")
engine = create_engine(DB_URL, pool_pre_ping=True)
PK = "SERIAL PRIMARY KEY" if IS_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"

classifier = KeywordClassifier()
responder = make_responder()


def now():  # Philippine time
    return datetime.utcnow() + timedelta(hours=8)


def hash_password(p):
    return hashlib.sha256(p.encode()).hexdigest()


def rows(sql, **p):
    with engine.begin() as c:
        return [dict(r._mapping) for r in c.execute(text(sql), p)]


def run(sql, **p):
    with engine.begin() as c:
        c.execute(text(sql), p)


def add_user(username, password, role, full_name, student_no):
    run("INSERT INTO users(username,password,role,full_name,student_no) VALUES(:u,:p,:r,:f,:s)",
        u=username, p=hash_password(password), r=role, f=full_name, s=student_no)


def init_db():
    run(f"""CREATE TABLE IF NOT EXISTS users(
        id {PK}, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL,
        full_name TEXT NOT NULL, student_no TEXT)""")
    run(f"""CREATE TABLE IF NOT EXISTS concerns(
        id {PK}, submitted_by TEXT NOT NULL, student_name TEXT NOT NULL, student_no TEXT NOT NULL,
        title TEXT NOT NULL, description TEXT NOT NULL, category TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'Pending', remarks TEXT DEFAULT '', ai_reply TEXT DEFAULT '',
        date_created TEXT NOT NULL)""")
    if not rows("SELECT id FROM users LIMIT 1"):
        add_user("teacher", "teacher123", "Teacher", "Teacher Demo", "")
        add_user("student", "student123", "Student", "Sabrina P. Dasco", "2026-0001")


init_db()


def login_required(role):
    def deco(f):
        @wraps(f)
        def wrapper(*a, **kw):
            u = session.get("user")
            if not u or u["role"] != role:
                if request.path.startswith("/api"):
                    return jsonify(error="Unauthorized"), 401
                return redirect("/")
            return f(*a, **kw)
        return wrapper
    return deco



@app.route("/")
def login_page():
    u = session.get("user")
    if u:
        return redirect("/teacher" if u["role"] == "Teacher" else "/student")
    return render_template("login.html", tagline=TAGLINE)


@app.route("/student")
@login_required("Student")
def student_page():
    return render_template("student.html", user=session["user"])


@app.route("/teacher")
@login_required("Teacher")
def teacher_page():
    return render_template("teacher.html", user=session["user"], categories=CATEGORIES, statuses=STATUSES)


@app.route("/logo/<name>.png")
def logo(name):
    data = LOGOS.get(name)
    if not data:
        return "", 404
    return Response(base64.b64decode(data), mimetype="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})

@app.route("/api/login", methods=["POST"])
def api_login():
    d = request.get_json()
    username, password = d.get("username", "").strip(), d.get("password", "")
    if not username or not password:
        return jsonify(error="Please enter your username and password."), 400
    r = rows("SELECT * FROM users WHERE username=:u AND password=:p", u=username, p=hash_password(password))
    if not r:
        return jsonify(error="Wrong username or password."), 401
    u = r[0]
    session["user"] = {"username": u["username"], "role": u["role"],
                       "full_name": u["full_name"], "student_no": u["student_no"] or ""}
    return jsonify(redirect="/teacher" if u["role"] == "Teacher" else "/student")


@app.route("/api/register", methods=["POST"])
def api_register():
    d = request.get_json()
    role, name = d.get("role"), d.get("full_name", "").strip()
    username, pw, extra = d.get("username", "").strip(), d.get("password", ""), d.get("extra", "").strip()
    if not name:
        return jsonify(error="Please enter your full name."), 400
    if len(username) < 4:
        return jsonify(error="Username must be at least 4 characters."), 400
    if len(pw) < 6:
        return jsonify(error="Password must be at least 6 characters."), 400
    if pw != d.get("confirm"):
        return jsonify(error="Passwords do not match."), 400
    if role == "Student" and not extra:
        return jsonify(error="Please enter your student number."), 400
    if role == "Teacher" and extra != TEACHER_CODE:
        return jsonify(error="Wrong teacher access code."), 400
    try:
        add_user(username, pw, role, name, extra if role == "Student" else "")
    except Exception:
        return jsonify(error="Username already exists."), 400
    return jsonify(ok=True)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/api/submit", methods=["POST"])
@login_required("Student")
def api_submit():
    d = request.get_json()
    title, desc = d.get("title", "").strip(), d.get("description", "").strip()
    if not title or not desc:
        return jsonify(error="Please fill in the title and description."), 400
    if len(desc) < 10:
        return jsonify(error="Description is too short (at least 10 characters)."), 400
    u = session["user"]
    category = classifier.predict(title + " " + desc)
    reply = responder.reply(title, desc, category)
    run("""INSERT INTO concerns(submitted_by,student_name,student_no,title,description,category,status,
           remarks,ai_reply,date_created) VALUES(:a,:b,:c,:d,:e,:f,'Pending','',:g,:h)""",
        a=u["username"], b=u["full_name"], c=u["student_no"], d=title, e=desc, f=category, g=reply,
        h=now().strftime("%Y-%m-%d %H:%M"))
    return jsonify(category=category, reply=reply)


@app.route("/api/my")
@login_required("Student")
def api_my():
    return jsonify(rows("SELECT * FROM concerns WHERE submitted_by=:u ORDER BY id DESC",
                        u=session["user"]["username"]))


@app.route("/api/ask", methods=["POST"])
@login_required("Student")
def api_ask():
    q = request.get_json().get("question", "").strip()
    if not q:
        return jsonify(error="empty"), 400
    category = classifier.predict(q)
    return jsonify(category=category, answer=responder.reply("", q, category))

@app.route("/api/concerns")
@login_required("Teacher")
def api_concerns():
    k = "%" + request.args.get("q", "").strip().lower() + "%"
    return jsonify(rows("""SELECT * FROM concerns WHERE LOWER(student_name) LIKE :k OR LOWER(title) LIKE :k
                           OR LOWER(category) LIKE :k OR LOWER(status) LIKE :k ORDER BY id DESC""", k=k))


@app.route("/api/concern/<int:cid>", methods=["POST", "DELETE"])
@login_required("Teacher")
def api_concern(cid):
    if request.method == "DELETE":
        run("DELETE FROM concerns WHERE id=:i", i=cid)
    else:
        d = request.get_json()
        run("UPDATE concerns SET category=:c,status=:s,remarks=:r WHERE id=:i",
            c=d["category"], s=d["status"], r=d.get("remarks", "").strip(), i=cid)
    return jsonify(ok=True)


@app.route("/api/summary")
@login_required("Teacher")
def api_summary():
    r = rows("SELECT category,status,date_created FROM concerns")
    return jsonify(
        total=len(r),
        statuses={s: len([x for x in r if x["status"] == s]) for s in STATUSES},
        chart=[[c, len([x for x in r if x["category"] == c])] for c in CATEGORIES],
        report=make_summary(r, now().strftime("%Y-%m-%d %H:%M")))


@app.route("/export.csv")
@login_required("Teacher")
def export_csv():
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["ID", "Student", "Student No", "Title", "Description", "Category",
                "Status", "AI Reply", "Teacher Remarks", "Date"])
    for r in rows("SELECT * FROM concerns ORDER BY id DESC"):
        w.writerow([r["id"], r["student_name"], r["student_no"], r["title"], r["description"],
                    r["category"], r["status"], r["ai_reply"], r["remarks"], r["date_created"]])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=concerns.csv"})


if __name__ == "__main__":
    app.run(debug=True)
