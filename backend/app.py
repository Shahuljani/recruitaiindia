import os
import uuid
import csv
import re
from io import StringIO
from datetime import datetime
from flask import Flask, render_template, redirect, session, flash, request, make_response
from PyPDF2 import PdfReader
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from firebase_config import db, firebase_auth

app = Flask(
    __name__,
    template_folder=os.path.join("..", "frontend", "templates"),
    static_folder=os.path.join("..", "frontend", "static")
)

app.secret_key = "recruitai_secure_key_v2"

# =============================
# HELPERS
# =============================

def parse_pdf(f):
    try:
        return "".join([p.extract_text() for p in PdfReader(f).pages])
    except:
        return ""

def get_ai_score(t1, t2):
    if not t1 or not t2:
        return 0
    try:
        return round(cosine_similarity(
            TfidfVectorizer().fit_transform([t1, t2])
        )[0][1] * 100, 2)
    except:
        return 0

def get_details(txt):
    email = re.search(r'[\w\.-]+@[\w\.-]+', txt)
    name = txt.split("\n")[0][:30]
    return name, email.group(0) if email else "Unknown"

def login_required():
    return 'user' in session

# =============================
# AUTH ROUTES
# =============================

@app.route('/')
def home():
    if login_required():
        return redirect('/dashboard')
    return render_template("login.html")

@app.route('/signup', methods=['POST'])
def signup():
    try:
        firebase_auth.create_user(
            email=request.form['email'],
            password=request.form['password']
        )
        flash("Signup successful. Please login.", "success")
    except Exception as e:
        flash(str(e), "error")
    return redirect('/')

@app.route('/ranking')
def ranking():

    if 'user' not in session:
        return redirect('/')

    uid = session['user']['id']

    docs = db.collection("candidates").where("user_id", "==", uid).stream()

    grouped = {}

    for d in docs:
        c = d.to_dict()
        c["id"] = d.id

        role = c.get("matched_role", "Unknown Role")

        if role not in grouped:
            grouped[role] = []

        grouped[role].append(c)

    # sort by score
    for role in grouped:
        grouped[role] = sorted(grouped[role], key=lambda x: x["score"], reverse=True)

    return render_template("ranking.html", grouped=grouped)

@app.route('/login', methods=['POST'])
def login():
    try:
        user = firebase_auth.get_user_by_email(request.form['email'])
        session['user'] = {'id': user.uid, 'email': user.email}
        return redirect('/dashboard')
    except:
        flash("Invalid credentials", "error")
        return redirect('/')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')
@app.route('/google-login', methods=['POST'])
def google_login():
    try:
        data = request.get_json()
        token = data.get("token")

        # Verify Firebase token
        decoded_token = firebase_auth.verify_id_token(token)

        # Create session
        session['user'] = {
            "id": decoded_token["uid"],
            "email": decoded_token.get("email")
        }

        return {"status": "success"}

    except Exception as e:
        return {"status": "error", "message": str(e)}, 401
# =============================
# DASHBOARD
# =============================

@app.route('/dashboard')
def dashboard():
    if not login_required():
        return redirect('/')

    uid = session['user']['id']

    jobs = list(db.collection("job_roles").where("user_id", "==", uid).stream())
    cands = list(db.collection("candidates").where("user_id", "==", uid).stream())

    stats = {
        "jobs": len(jobs),
        "cands": len(cands),
        "short": len([c for c in cands if c.to_dict().get("status") == "Shortlisted"]),
        "avg": round(sum([c.to_dict().get("score", 0) for c in cands]) / len(cands), 1) if cands else 0
    }

    recent = sorted(
        [c.to_dict() | {"id": c.id} for c in cands],
        key=lambda x: x.get("created_at", datetime.utcnow()),
        reverse=True
    )[:5]

    return render_template("dashboard.html", stats=stats, recent=recent)

# =============================
# JOBS
# =============================

@app.route('/job_roles')
def job_roles():
    if not login_required():
        return redirect('/')

    uid = session['user']['id']
    jobs = db.collection("job_roles").where("user_id", "==", uid).stream()
    jobs = [j.to_dict() | {"id": j.id} for j in jobs]

    return render_template("jobs.html", jobs=jobs)

@app.route('/add_job', methods=['POST'])
def add_job():
    uid = session['user']['id']

    for f in request.files.getlist('files'):
        text = parse_pdf(f)
        if text:
            db.collection("job_roles").add({
                "user_id": uid,
                "title": f.filename,
                "description": text,
                "created_at": datetime.utcnow()
            })
    return redirect('/job_roles')
@app.route('/admin')
def admin():

    if 'user' not in session:
        return redirect('/')

    uid = session['user']['id']

    jobs = list(db.collection("job_roles").where("user_id","==",uid).stream())
    cands = list(db.collection("candidates").where("user_id","==",uid).stream())

    stats = {
        "jobs": len(jobs),
        "cands": len(cands)
    }

    return render_template("admin.html", stats=stats)

@app.route('/delete_job', methods=['POST'])
def delete_job():
    db.collection("job_roles").document(request.form['id']).delete()
    return redirect('/job_roles')

# =============================
# AI SCREENING
# =============================

@app.route('/ai_screening')
def screening():
    return render_template("screen.html")

@app.route('/process', methods=['POST'])
def process():
    uid = session['user']['id']
    jobs = db.collection("job_roles").where("user_id", "==", uid).stream()
    jobs = [j.to_dict() | {"id": j.id} for j in jobs]

    for f in request.files.getlist('resumes'):
        txt = parse_pdf(f)
        if not txt:
            continue

        best_job = None
        best_score = -1

        for j in jobs:
            score = get_ai_score(txt, j['description'])
            if score > best_score:
                best_score = score
                best_job = j

        if best_job:
            name, email = get_details(txt)
            status = "Shortlisted" if best_score >= 75 else "On Hold" if best_score >= 50 else "Rejected"

            db.collection("candidates").add({
                "user_id": uid,
                "name": name,
                "email": email,
                "job_role_id": best_job['id'],
                "matched_role": best_job['title'],
                "score": best_score,
                "status": status,
                "resume_text": txt[:5000],
                "created_at": datetime.utcnow()
            })

    return redirect('/candidates')

# =============================
# CANDIDATES
# =============================

@app.route('/candidates')
def candidates():
    uid = session['user']['id']
    cands = db.collection("candidates").where("user_id", "==", uid).stream()
    cands = [c.to_dict() | {"id": c.id} for c in cands]

    return render_template("candidates.html", cands=cands)

@app.route('/status', methods=['POST'])
def update_status():
    db.collection("candidates").document(request.form['id']).update({
        "status": request.form['status']
    })
    return redirect('/candidates')

@app.route('/del_cand', methods=['POST'])
def delete_candidate():
    db.collection("candidates").document(request.form['id']).delete()
    return redirect('/candidates')

# =============================
# EXPORT
# =============================

@app.route('/export')
def export_csv():
    uid = session['user']['id']
    cands = db.collection("candidates").where("user_id", "==", uid).stream()

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(["Name", "Email", "Role", "Score", "Status"])

    for c in cands:
        d = c.to_dict()
        cw.writerow([d.get("name"), d.get("email"), d.get("matched_role"), d.get("score"), d.get("status")])

    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=candidates.csv"
    output.headers["Content-type"] = "text/csv"
    return output

# =============================

if __name__ == "__main__":
    app.run(debug=True)