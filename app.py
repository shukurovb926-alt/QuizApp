# app.py
import sqlite3
import json
import time
from flask import Flask, g, render_template, request, redirect, url_for, jsonify

DB = 'quiz.db'
app = Flask(__name__)

def get_db():
    db = getattr(g, '_db', None)
    if db is None:
        db = g._db = sqlite3.connect(DB, check_same_thread=False)
        db.row_factory = sqlite3.Row
    return db

def init_db():
    db = get_db()
    cur = db.cursor()
    cur.execute('''
    CREATE TABLE IF NOT EXISTS quizzes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        current_q INTEGER DEFAULT -1,
        started_at INTEGER DEFAULT 0,
        q_time INTEGER DEFAULT 15
    )''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        quiz_id INTEGER,
        qtext TEXT,
        choices TEXT, -- json list
        correct INTEGER
    )''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS participants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        quiz_id INTEGER,
        name TEXT,
        surname TEXT,
        joined_at INTEGER
    )''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS answers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        participant_id INTEGER,
        question_id INTEGER,
        answer INTEGER,
        answered_at INTEGER
    )''')
    db.commit()

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_db', None)
    if db:
        db.close()

@app.route('/')
def index():
    return render_template('join.html')

# Participant posts name and chooses quiz (we keep single default quiz for simplicity)
@app.route('/register', methods=['POST'])
def register():
    name = request.form.get('name','').strip()
    surname = request.form.get('surname','').strip()
    quiz_id = int(request.form.get('quiz_id', 1))
    if not name:
        return "Name required", 400
    db = get_db()
    cur = db.cursor()
    cur.execute("INSERT INTO participants (quiz_id,name,surname,joined_at) VALUES (?,?,?,?)",
                (quiz_id, name, surname, int(time.time())))
    db.commit()
    pid = cur.lastrowid
    return redirect(url_for('participant', pid=pid))

@app.route('/p/<int:pid>')
def participant(pid):
    return render_template('participant.html', pid=pid)

# API: get current question for participant
@app.route('/api/current/<int:pid>')
def api_current(pid):
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT quiz_id FROM participants WHERE id=?", (pid,))
    r = cur.fetchone()
    if not r:
        return jsonify(error="participant not found"), 404
    quiz_id = r['quiz_id']
    cur.execute("SELECT current_q, q_time, started_at FROM quizzes WHERE id=?", (quiz_id,))
    q = cur.fetchone()
    if not q:
        return jsonify(error="quiz not found"), 404
    current_q = q['current_q']
    q_time = q['q_time']
    started_at = q['started_at']
    if current_q < 0:
        return jsonify(state="waiting")
    # get question
    cur.execute("SELECT * FROM questions WHERE quiz_id=? ORDER BY id LIMIT 1 OFFSET ?", (quiz_id, current_q))
    question = cur.fetchone()
    if not question:
        return jsonify(state="finished")
    qid = question['id']
    elapsed = int(time.time()) - started_at
    # compute question start based on index
    qstart = current_q * q_time
    time_left = q_time - (elapsed - qstart)
    if time_left < 0:
        time_left = 0
    return jsonify(
        state="question",
        question_id=qid,
        qtext=question['qtext'],
        choices=json.loads(question['choices']),
        time_left=time_left,
        question_index=current_q
    )

# API: submit answer
@app.route('/api/answer', methods=['POST'])
def api_answer():
    data = request.json or {}
    pid = int(data.get('participant_id', 0))
    qid = int(data.get('question_id', 0))
    answer = int(data.get('answer', -1))
    if not pid or not qid:
        return jsonify(error="missing"), 400
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT id FROM answers WHERE participant_id=? AND question_id=?", (pid, qid))
    if cur.fetchone():
        return jsonify(status="already answered")
    cur.execute("INSERT INTO answers (participant_id,question_id,answer,answered_at) VALUES (?,?,?,?)",
                (pid, qid, answer, int(time.time())))
    db.commit()
    return jsonify(status="ok")

# Admin UI
@app.route('/admin')
def admin():
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM quizzes")
    quizzes = cur.fetchall()
    return render_template('admin.html', quizzes=quizzes)

# Create simple quiz with questions (from admin form)
@app.route('/admin/create', methods=['POST'])
def admin_create():
    title = request.form.get('title', 'Quiz 1')
    q_time = int(request.form.get('q_time', 15))
    # questions entered as JSON lines or simple format: one question per line with choices separated by ||
    raw = request.form.get('questions','').strip()
    db = get_db()
    cur = db.cursor()
    cur.execute("INSERT INTO quizzes (title,current_q,started_at,q_time) VALUES (?,?,?,?)",
                (title, -1, 0, q_time))
    qid = cur.lastrowid
    # parse lines like: Question?||A||B||C||D||2  (last number = correct index, 0-based)
    for line in raw.splitlines():
        parts = [p.strip() for p in line.split('||') if p.strip()]
        if len(parts) < 3:
            continue
        qtext = parts[0]
        choices = parts[1:-1]
        try:
            correct = int(parts[-1])
        except:
            correct = 0
        cur.execute("INSERT INTO questions (quiz_id,qtext,choices,correct) VALUES (?,?,?,?)",
                    (qid, qtext, json.dumps(choices), correct))
    db.commit()
    return redirect(url_for('admin'))

# Start quiz (set current_q to 0 and started_at = now)
@app.route('/admin/start/<int:quiz_id>', methods=['POST'])
def admin_start(quiz_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("UPDATE quizzes SET current_q=0, started_at=? WHERE id=?", (int(time.time()), quiz_id))
    db.commit()
    return redirect(url_for('admin'))

# Advance question (admin click)
@app.route('/admin/next/<int:quiz_id>', methods=['POST'])
def admin_next(quiz_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT current_q FROM quizzes WHERE id=?", (quiz_id,))
    r = cur.fetchone()
    if not r:
        return "no quiz", 404
    cur_q = r['current_q']
    cur.execute("SELECT COUNT(*) as c FROM questions WHERE quiz_id=?", (quiz_id,))
    total = cur.fetchone()['c']
    if cur_q + 1 >= total:
        cur.execute("UPDATE quizzes SET current_q=-1 WHERE id=?", (quiz_id,))
    else:
        cur.execute("UPDATE quizzes SET current_q=current_q+1 WHERE id=?", (quiz_id,))
    db.commit()
    return redirect(url_for('admin'))

# Admin view stats
@app.route('/admin/stats/<int:quiz_id>')
def admin_stats(quiz_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT p.id, p.name, p.surname, COUNT(a.id) as answers FROM participants p LEFT JOIN answers a ON a.participant_id=p.id WHERE p.quiz_id=? GROUP BY p.id ORDER BY answers DESC", (quiz_id,))
    rows = [dict(r) for r in cur.fetchall()]
    return jsonify(rows)

if __name__ == '__main__':
    with app.app_context():
        init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)
