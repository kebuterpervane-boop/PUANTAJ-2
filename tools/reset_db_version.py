import sqlite3
import os

db_path = r'C:\Users\slims\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\LocalCache\Roaming\SaralGroup\PuantajApp\puantaj.db'

if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # Check version
    cur.execute('PRAGMA user_version')
    version = cur.fetchone()[0]
    print(f"Current database version: {version}")
    
    # Reset version to 0 to force migrations to run
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()
    
    print("Database version reset to 0 - migrations will run on next app start")
else:
    print(f"Database not found: {db_path}")
