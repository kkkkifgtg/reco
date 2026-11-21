import sqlite3
c = sqlite3.connect("reco.db")
print("liked rows:", c.execute("SELECT COUNT(*) FROM files WHERE zone='liked' AND present=1").fetchone()[0])
print("books:", c.execute("SELECT COUNT(*) FROM books").fetchone()[0])
