import sqlite3
import unittest

from backend.app.database import _set_sqlite_connection_pragmas


class SqliteConnectionPragmaTests(unittest.TestCase):
    def test_connection_enables_foreign_keys_and_busy_timeout(self):
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)

        _set_sqlite_connection_pragmas(connection)

        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
        self.assertEqual(foreign_keys, 1)
        self.assertEqual(busy_timeout, 5000)
