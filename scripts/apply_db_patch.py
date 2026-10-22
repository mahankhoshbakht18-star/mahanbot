import os
import sqlite3

with open('database.py', 'r', encoding='utf-8') as f:
    text = f.read()

target = 'class DBHandler:\n    _settings_cache = {}'
assert target in text, 'Target not found in database.py'

replacement = '''class DBHandler:
    _settings_cache = {}

    @classmethod
    def backup_database(cls, prefix: str = "backup") -> str:
        """Create a backup of the current active database."""
        import os
        import sqlite3
        import database
        from core.db_migration import create_database_backup
        target_path = database.DB_PATH
        if not os.path.exists(target_path):
            os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
            conn = sqlite3.connect(target_path)
            conn.close()
        return create_database_backup(target_path, prefix=prefix)
'''

text = text.replace(target, replacement, 1)
with open('database.py', 'w', encoding='utf-8') as f:
    f.write(text)

print("SUCCESS: database.py updated")
