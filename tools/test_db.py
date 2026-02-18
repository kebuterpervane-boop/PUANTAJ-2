import sqlite3
import os

db_path = r'C:\Users\slims\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\LocalCache\Roaming\SaralGroup\PuantajApp\puantaj.db'

if os.path.exists(db_path):
    print(f"Database exists: {db_path}")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # Check mesai_katsayilari table schema
    try:
        cur.execute('PRAGMA table_info(mesai_katsayilari)')
        cols = cur.fetchall()
        print('mesai_katsayilari columns:')
        for col in cols:
            print(f'  {col[1]}: {col[2]}')
        
        # Try to select from it
        cur.execute('SELECT * FROM mesai_katsayilari')
        rows = cur.fetchall()
        print(f'\nData in mesai_katsayilari:')
        for row in rows:
            print(f'  {row}')
    except Exception as e:
        print(f'Error: {e}')
    
    conn.close()
else:
    print(f"Database not found: {db_path}")
