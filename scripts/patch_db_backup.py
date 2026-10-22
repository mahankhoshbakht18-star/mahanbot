import os

with open('database.py', 'r', encoding='utf-8') as f:
    text = f.read()

target = 'class DBHandler:\n    _settings_cache = {}'
if target not in text:
    target = 'class DBHandler:\r\n    _settings_cache = {}'

replacement = '''class DBHandler:
    _settings_cache = {}

    @classmethod
    def backup_database(cls, prefix: str = "backup") -> str:
        """Create a backup of the current active database."""
        from core.db_migration import create_database_backup
        import database
        target_path = database.DB_PATH
        if not os.path.exists(target_path):
            os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
            import sqlite3
            conn = sqlite3.connect(target_path)
            conn.close()
        return create_database_backup(target_path, prefix=prefix)
'''

if target in text:
    text = text.replace(target, replacement, 1)
    with open('database.py', 'w', encoding='utf-8') as f:
        f.write(text)
    print('SUCCESS: Added backup_database to DBHandler')
else:
    print('TARGET NOT FOUND')
