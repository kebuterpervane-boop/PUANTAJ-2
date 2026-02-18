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
    
    conn.close()
else:
    print(f"Database not found: {db_path}")
