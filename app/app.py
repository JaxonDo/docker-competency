from flask import Flask, jsonify, request
import psycopg2
import redis
import os
import time

app = Flask(__name__)

# Redis connection
cache = redis.Redis(
    host=os.environ.get("REDIS_HOST", "redis"),
    port=6379,
    decode_responses=True,
)

def get_db_connection():
    # retry connection in case postgres isn't ready yet
    retries = 5
    for i in range(retries):
        try:
            conn = psycopg2.connect(
                host=os.environ.get("DB_HOST", "db"),
                database=os.environ.get("DB_NAME", "devdb"),
                user=os.environ.get("DB_USER", "devuser"),
                password=os.environ.get("DB_PASSWORD", "devpass"),
            )
            return conn
        except psycopg2.OperationalError:
            if i < retries - 1:
                time.sleep(2)
            else:
                raise

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            email VARCHAR(100) UNIQUE NOT NULL
        );
        """
    )
    conn.commit()
    cur.close()
    conn.close()

@app.route("/")
def index():
    visits = cache.incr("visit_count")
    return jsonify({"message": "Welcome to the Flask + Docker demo!", "visits": visits})

@app.route("/health")
def health():
    # check if redis and postgres are reachable
    status = {"flask": "ok", "redis": "ok", "postgres": "ok"}
    try:
        cache.ping()
    except redis.ConnectionError:
        status["redis"] = "unreachable"
    try:
        conn = get_db_connection()
        conn.close()
    except Exception:
        status["postgres"] = "unreachable"

    all_ok = all(v == "ok" for v in status.values())
    return jsonify(status), 200 if all_ok else 503

@app.route("/users", methods=["GET"])
def get_users():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name, email FROM users ORDER BY id;")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    users = [{"id": r[0], "name": r[1], "email": r[2]} for r in rows]
    return jsonify(users)

@app.route("/users", methods=["POST"])
def create_user():
    data = request.get_json()
    if not data or "name" not in data or "email" not in data:
        return jsonify({"error": "name and email are required"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (name, email) VALUES (%s, %s) RETURNING id;",
            (data["name"], data["email"]),
        )
        user_id = cur.fetchone()[0]
        conn.commit()
    except psycopg2.IntegrityError:
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({"error": "email already exists"}), 409
    cur.close()
    conn.close()

    # Invalidate cached user count
    cache.delete("user_count")
    return jsonify({"id": user_id, "name": data["name"], "email": data["email"]}), 201

@app.route("/stats")
def stats():
    user_count = cache.get("user_count")
    if user_count is None:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users;")
        user_count = cur.fetchone()[0]
        cur.close()
        conn.close()
        cache.set("user_count", user_count, ex=30)  # cache for 30 seconds
    else:
        user_count = int(user_count)

    visits = cache.get("visit_count") or 0
    return jsonify({"total_users": user_count, "total_visits": int(visits)})

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
