import sqlite3
from flask import Flask, render_template, request, redirect, url_for, session, flash, g
from werkzeug.security import generate_password_hash, check_password_hash
import os

app = Flask(__name__)
app.config["SECRET_KEY"] = "college_attendance_secret_key"
app.config["DATABASE"] = os.path.join(app.root_path, "attendance.db")


def get_db():
    db = getattr(g, "db", None)
    if db is None:
        db = g.db = sqlite3.connect(app.config["DATABASE"])
        db.row_factory = sqlite3.Row
    return db


def init_db():
    db = get_db()
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('teacher', 'student')),
        full_name TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT UNIQUE NOT NULL,
        full_name TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS attendance_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_name TEXT NOT NULL,
        date TEXT NOT NULL,
        teacher_id INTEGER NOT NULL,
        FOREIGN KEY(teacher_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS attendance_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL,
        student_id INTEGER NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('present', 'absent', 'late')),
        comments TEXT,
        FOREIGN KEY(session_id) REFERENCES attendance_sessions(id),
        FOREIGN KEY(student_id) REFERENCES students(id)
    );
    """)
    db.commit()
    seed_data(db)


def seed_data(db):
    teacher = db.execute("SELECT * FROM users WHERE role = 'teacher'").fetchone()
    if teacher is None:
        hashed = generate_password_hash("teacher123")
        db.execute(
            "INSERT INTO users (username, password, role, full_name) VALUES (?, ?, 'teacher', ?)" ,
            ("teacher", hashed, "Professor Ava")
        )
        db.commit()


@app.teardown_appcontext
def close_db(exception=None):
    db = getattr(g, "db", None)
    if db is not None:
        db.close()


def query_db(query, args=(), one=False):
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv


@app.before_request
def ensure_db():
    if not os.path.exists(app.config["DATABASE"]):
        init_db()
    else:
        get_db()


@app.route("/")
def home():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    teacher_count = query_db("SELECT COUNT(*) AS total FROM users WHERE role = 'teacher'", one=True)["total"]
    remaining_teachers = 10 - teacher_count

    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        role = request.form["role"]

        user = query_db("SELECT * FROM users WHERE username = ? AND role = ?", (username, role), one=True)
        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            session["full_name"] = user["full_name"]
            return redirect(url_for("dashboard"))

        flash("Invalid username, password or role.")

    return render_template("login.html", teacher_count=teacher_count, remaining_teachers=remaining_teachers)


@app.route("/register", methods=["GET", "POST"])
def register():
    teacher_count = query_db("SELECT COUNT(*) AS total FROM users WHERE role = 'teacher'", one=True)["total"]
    remaining_teachers = 10 - teacher_count
    if remaining_teachers <= 0:
        flash("Teacher registration limit has been reached.")
        return redirect(url_for("login"))

    if request.method == "POST":
        full_name = request.form["full_name"].strip()
        username = request.form["username"].strip()
        password = request.form["password"]

        if not (full_name and username and password):
            flash("Name, username, and password are required.")
        else:
            try:
                hashed = generate_password_hash(password)
                db = get_db()
                db.execute(
                    "INSERT INTO users (username, password, role, full_name) VALUES (?, ?, 'teacher', ?)",
                    (username, hashed, full_name)
                )
                db.commit()
                flash("Teacher account created successfully. Please login.")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("The chosen username is already taken. Please choose a different one.")

    return render_template("register.html", remaining_teachers=remaining_teachers)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
def dashboard():
    if not session.get("user_id"):
        return redirect(url_for("login"))

    if session["role"] == "teacher":
        student_count = query_db("SELECT COUNT(*) AS total FROM students", one=True)["total"]
        session_count = query_db("SELECT COUNT(*) AS total FROM attendance_sessions", one=True)["total"]
        attendance_summary = query_db(
            "SELECT status, COUNT(*) AS count FROM attendance_records GROUP BY status"
        )
        summary = {item["status"]: item["count"] for item in attendance_summary}
        recent_sessions = query_db(
            "SELECT id, session_name, date, "
            "(SELECT COUNT(*) FROM attendance_records WHERE attendance_records.session_id = attendance_sessions.id) AS total_students, "
            "(SELECT COUNT(*) FROM attendance_records WHERE attendance_records.session_id = attendance_sessions.id AND status = 'present') AS present_count "
            "FROM attendance_sessions ORDER BY date DESC LIMIT 5"
        )
        return render_template(
            "teacher_dashboard.html",
            student_count=student_count,
            session_count=session_count,
            summary=summary,
            recent_sessions=recent_sessions,
        )

    student = query_db(
        "SELECT students.*, users.username FROM students JOIN users ON students.user_id = users.id WHERE users.id = ?",
        (session["user_id"],), one=True
    )
    if student:
        records = query_db(
            "SELECT attendance_sessions.session_name, attendance_sessions.date, attendance_records.status, attendance_records.comments "
            "FROM attendance_records "
            "JOIN attendance_sessions ON attendance_records.session_id = attendance_sessions.id "
            "WHERE attendance_records.student_id = ? ORDER BY attendance_sessions.date DESC",
            (student["id"],)
        )
        totals = query_db(
            "SELECT status, COUNT(*) AS count FROM attendance_records WHERE student_id = ? GROUP BY status",
            (student["id"],)
        )
        totals = {item["status"]: item["count"] for item in totals}
        total_sessions = sum(totals.values())
        present = totals.get("present", 0)
        attendance_percent = round((present / total_sessions) * 100, 1) if total_sessions else 0
        return render_template(
            "student_dashboard.html",
            student=student,
            records=records,
            totals=totals,
            total_sessions=total_sessions,
            attendance_percent=attendance_percent,
        )

    flash("Student profile not found. Please ask teacher to register your account.")
    return redirect(url_for("logout"))


@app.route("/teacher/session/<int:session_id>")
def session_summary(session_id):
    if session.get("role") != "teacher":
        return redirect(url_for("login"))

    session_data = query_db("SELECT * FROM attendance_sessions WHERE id = ?", (session_id,), one=True)
    if not session_data:
        flash("Attendance session not found.")
        return redirect(url_for("attendance_session"))

    records = query_db(
        "SELECT students.student_id, students.full_name, attendance_records.status, attendance_records.comments "
        "FROM attendance_records "
        "JOIN students ON attendance_records.student_id = students.id "
        "WHERE attendance_records.session_id = ? ORDER BY students.full_name",
        (session_id,)
    )
    totals = query_db(
        "SELECT status, COUNT(*) AS count FROM attendance_records WHERE session_id = ? GROUP BY status",
        (session_id,)
    )
    totals = {item["status"]: item["count"] for item in totals}
    return render_template(
        "session_summary.html",
        session_data=session_data,
        records=records,
        totals=totals,
    )


@app.route("/teacher/attendance/report")
def attendance_report():
    if session.get("role") != "teacher":
        return redirect(url_for("login"))

    sessions = query_db(
        "SELECT attendance_sessions.id, attendance_sessions.session_name, attendance_sessions.date, "
        "SUM(CASE WHEN attendance_records.status = 'present' THEN 1 ELSE 0 END) AS present_count, "
        "SUM(CASE WHEN attendance_records.status = 'absent' THEN 1 ELSE 0 END) AS absent_count, "
        "SUM(CASE WHEN attendance_records.status = 'late' THEN 1 ELSE 0 END) AS late_count, "
        "COUNT(attendance_records.id) AS total_count "
        "FROM attendance_sessions "
        "LEFT JOIN attendance_records ON attendance_records.session_id = attendance_sessions.id "
        "GROUP BY attendance_sessions.id "
        "ORDER BY attendance_sessions.date DESC"
    )
    return render_template("attendance_report.html", sessions=sessions)


@app.route("/teacher/students", methods=["GET", "POST"])
def manage_students():
    if session.get("role") != "teacher":
        return redirect(url_for("login"))

    db = get_db()
    if request.method == "POST":
        student_id = request.form["student_id"].strip()
        full_name = request.form["full_name"].strip()
        username = request.form["username"].strip()
        password = request.form["password"]

        if not (student_id and full_name and username and password):
            flash("All fields are required.")
        else:
            try:
                hashed = generate_password_hash(password)
                db.execute(
                    "INSERT INTO users (username, password, role, full_name) VALUES (?, ?, 'student', ?)",
                    (username, hashed, full_name)
                )
                user_id = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()["id"]
                db.execute(
                    "INSERT INTO students (student_id, full_name, user_id) VALUES (?, ?, ?)",
                    (student_id, full_name, user_id)
                )
                db.commit()
                flash("Student registered successfully.")
            except sqlite3.IntegrityError:
                flash("A student or username with those details already exists.")

    students = query_db(
        "SELECT students.*, users.username, "
        "SUM(CASE WHEN attendance_records.status = 'present' THEN 1 ELSE 0 END) AS present_count, "
        "SUM(CASE WHEN attendance_records.status = 'absent' THEN 1 ELSE 0 END) AS absent_count, "
        "SUM(CASE WHEN attendance_records.status = 'late' THEN 1 ELSE 0 END) AS late_count, "
        "COUNT(attendance_records.id) AS total_count "
        "FROM students "
        "JOIN users ON students.user_id = users.id "
        "LEFT JOIN attendance_records ON students.id = attendance_records.student_id "
        "GROUP BY students.id "
        "ORDER BY students.full_name"
    )
    return render_template("manage_students.html", students=students)


@app.route("/teacher/student/delete/<int:student_id>", methods=["POST"])
def delete_student(student_id):
    if session.get("role") != "teacher":
        return redirect(url_for("login"))

    db = get_db()
    student = query_db("SELECT * FROM students WHERE id = ?", (student_id,), one=True)
    if not student:
        flash("Student not found.")
        return redirect(url_for("manage_students"))

    db.execute("DELETE FROM attendance_records WHERE student_id = ?", (student_id,))
    db.execute("DELETE FROM students WHERE id = ?", (student_id,))
    db.execute("DELETE FROM users WHERE id = ?", (student["user_id"],))
    db.commit()
    flash("Student deleted successfully.")
    return redirect(url_for("manage_students"))


@app.route("/teacher/attendance", methods=["GET", "POST"])
def attendance_session():
    if session.get("role") != "teacher":
        return redirect(url_for("login"))

    db = get_db()
    if request.method == "POST":
        session_name = request.form["session_name"].strip()
        date = request.form["date"].strip()
        if not session_name or not date:
            flash("Session name and date are required.")
        else:
            db.execute(
                "INSERT INTO attendance_sessions (session_name, date, teacher_id) VALUES (?, ?, ?)",
                (session_name, date, session["user_id"])
            )
            db.commit()
            flash("Attendance session created. Mark attendance below.")
            return redirect(url_for("mark_attendance", session_id=db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]))

    sessions = query_db("SELECT * FROM attendance_sessions ORDER BY date DESC")
    return render_template("attendance_session.html", sessions=sessions)


@app.route("/teacher/session/delete/<int:session_id>", methods=["POST"])
def delete_session(session_id):
    if session.get("role") != "teacher":
        return redirect(url_for("login"))

    db = get_db()
    session_data = query_db("SELECT * FROM attendance_sessions WHERE id = ?", (session_id,), one=True)
    if not session_data:
        flash("Attendance session not found.")
        return redirect(url_for("attendance_session"))

    db.execute("DELETE FROM attendance_records WHERE session_id = ?", (session_id,))
    db.execute("DELETE FROM attendance_sessions WHERE id = ?", (session_id,))
    db.commit()
    flash("Attendance session deleted successfully.")
    return redirect(url_for("attendance_session"))


@app.route("/teacher/attendance/mark/<int:session_id>", methods=["GET", "POST"])
def mark_attendance(session_id):
    if session.get("role") != "teacher":
        return redirect(url_for("login"))

    db = get_db()
    session_data = query_db("SELECT * FROM attendance_sessions WHERE id = ?", (session_id,), one=True)
    students = query_db("SELECT * FROM students ORDER BY full_name")
    if not session_data:
        flash("Attendance session not found.")
        return redirect(url_for("attendance_session"))

    if request.method == "POST":
        for student in students:
            status = request.form.get(f"status_{student['id']}", "absent")
            comments = request.form.get(f"comments_{student['id']}", "").strip()
            existing = query_db(
                "SELECT * FROM attendance_records WHERE session_id = ? AND student_id = ?",
                (session_id, student["id"]), one=True
            )
            if existing:
                db.execute(
                    "UPDATE attendance_records SET status = ?, comments = ? WHERE id = ?",
                    (status, comments, existing["id"])
                )
            else:
                db.execute(
                    "INSERT INTO attendance_records (session_id, student_id, status, comments) VALUES (?, ?, ?, ?)",
                    (session_id, student["id"], status, comments)
                )
        db.commit()
        flash("Attendance saved successfully.")

    records = {r["student_id"]: r for r in query_db(
        "SELECT * FROM attendance_records WHERE session_id = ?", (session_id,)
    )}
    return render_template("mark_attendance.html", session_data=session_data, students=students, records=records)


@app.route("/teacher/attendance/view")
def view_attendance():
    if session.get("role") != "teacher":
        return redirect(url_for("login"))

    records = query_db(
        "SELECT attendance_sessions.session_name, attendance_sessions.date, students.student_id, students.full_name, attendance_records.status, attendance_records.comments "
        "FROM attendance_records "
        "JOIN attendance_sessions ON attendance_records.session_id = attendance_sessions.id "
        "JOIN students ON attendance_records.student_id = students.id "
        "ORDER BY attendance_sessions.date DESC, students.full_name"
    )
    return render_template("view_attendance.html", records=records)


if __name__ == "__main__":
    app.run(debug=True)
