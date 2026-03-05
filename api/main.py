from fastapi import FastAPI
import sqlite3

app = FastAPI()

def get_db():
    conn = sqlite3.connect("items.db")
    conn.row_factory = sqlite3.Row
    return conn

@app.get("/")
def root():
    return {"status": "API working"}

@app.get("/items")
def get_items():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM items")
    rows = cursor.fetchall()
    conn.close()

    result = []
    for r in rows:
        result.append(dict(r))

    return result
