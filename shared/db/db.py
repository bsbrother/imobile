#
# Database Utils
#

import os
import sys
import sqlite3
import datetime
from loguru import logger
from contextlib import contextmanager
from typing import Iterator, Optional, List, Any, Union, Dict
from dotenv import load_dotenv

logger.remove()
logger.add(sys.stderr, level='INFO')

load_dotenv()
db_path = os.getenv('DB_IMOBILE_FILE')
dbtest_path = os.getenv('DBTEST_IMOBILE_FILE')
if not os.path.exists(db_path):
    db_path = os.path.join(os.path.dirname(__file__), 'imobile.db')
if not os.path.exists(db_path):
    raise FileNotFoundError(f"Database file not found at {db_path}")
if not os.path.exists(dbtest_path):
    dbtest_path = os.path.join(os.path.dirname(__file__), 'test_imobile.db')
if not os.path.exists(dbtest_path):
    raise FileNotFoundError(f"Database file not found at {dbtest_path}")
DB_IMOBILE_FILE = db_path
DBTEST_IMOBILE_FILE = dbtest_path
DB_IMOBILE_SQL = os.path.splitext(DB_IMOBILE_FILE)[0] + '.sql'

class DatabaseManager:
    def __init__(self, db_path: str | None = None, **kwargs):
        """
        Initialize DatabaseManager

        Args:
            db_path: Path to SQLite database file
            **kwargs: Additional connection parameters
        """

        def adapt_datetime(dt):
            return dt.isoformat()

        def convert_datetime(text):
            return datetime.datetime.fromisoformat(text.decode())

        # Register adapters
        sqlite3.register_adapter(datetime.datetime, adapt_datetime)
        sqlite3.register_converter("datetime", convert_datetime)
        kwargs['detect_types'] = sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES
        self.db_path = db_path
        self.connection_kwargs = kwargs
        self._setup_database()

    def _setup_database(self) -> None:
        """Setup database with required tables and configuration"""
        with self.connect() as conn:
            # Enable foreign keys and other pragmas
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            # You can add table creation logic here if needed
            logger.debug(f"Database initialized at {self.db_path}")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """
        Context manager for database connection with proper datetime handling

        Yields:
            sqlite3.Connection: Database connection

        Raises:
            sqlite3.Error: If connection or transaction fails
        """
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, **self.connection_kwargs)
            conn.row_factory = sqlite3.Row
            logger.debug("Database connection established")

            yield conn
            conn.commit()
            logger.debug("Transaction committed")

        except sqlite3.Error as e:
            if conn:
                conn.rollback()
                logger.error(f"Database error occurred, transaction rolled back: {e}")
            raise e
        finally:
            if conn:
                conn.close()
                logger.debug("Database connection closed")

    @contextmanager
    def cursor(self, commit: bool = True) -> Iterator[sqlite3.Cursor]:
        """
        Context manager for database cursor

        Args:
            commit: Whether to commit transaction on success

        Yields:
            sqlite3.Cursor: Database cursor
        """
        with self.connect() as conn:
            cursor = conn.cursor()
            try:
                yield cursor
                if commit:
                    conn.commit()
            except Exception:
                if commit:
                    conn.rollback()
                raise
            finally:
                cursor.close()

    def execute(self, query: str, params: Union[tuple, Dict[str, Any], None] = None,
                commit: bool = True) -> sqlite3.Cursor:
        """
        Execute a query without returning results

        Args:
            query: SQL query string
            params: Query parameters
            commit: Whether to commit the transaction

        Returns:
            sqlite3.Cursor: The executed cursor

        Example:
            db.execute("INSERT INTO users (name) VALUES (?)", ("John",))
        """
        with self.cursor(commit=commit) as cursor:
            cursor.execute(query, params or ())
            return cursor

    def fetch_one(self, query: str, params: Union[tuple, Dict[str, Any], None] = None) -> Optional[sqlite3.Row]:
        """
        Fetch a single row

        Args:
            query: SQL query string
            params: Query parameters

        Returns:
            Optional[sqlite3.Row]: Single row or None if no results. need dict(result).

        Example:
            user = db.fetch_one("SELECT * FROM users WHERE id = ?", (1,))
        """
        with self.cursor() as cursor:
            cursor.execute(query, params or ())
            return cursor.fetchone()

    def fetch_all(self, query: str, params: Union[tuple, Dict[str, Any], None] = None) -> List[sqlite3.Row]:
        """
        Fetch all rows

        Args:
            query: SQL query string
            params: Query parameters

        Returns:
            List[sqlite3.Row]: List of rows

        Example:
            users = db.fetch_all("SELECT * FROM users WHERE active = ?", (True,))
        """
        with self.cursor() as cursor:
            cursor.execute(query, params or ())
            return cursor.fetchall()

    def fetch_value(self, query: str, params: Union[tuple, Dict[str, Any], None] = None,
                   default: Any = None) -> Any:
        """
        Fetch a single value from the first column of the first row

        Args:
            query: SQL query string
            params: Query parameters
            default: Default value if no results

        Returns:
            Any: Single value from query

        Example:
            count = db.fetch_value("SELECT COUNT(*) FROM users")
        """
        row = self.fetch_one(query, params)
        return row[0] if row and len(row) > 0 else default

    def insert(self, table: str, data: Dict[str, Any], commit: bool = True) -> int:
        """
        Insert a row into a table

        Args:
            table: Table name
            data: Dictionary of column-value pairs
            commit: Whether to commit the transaction

        Returns:
            int: ID of the inserted row

        Example:
            user_id = db.insert("users", {"name": "John", "email": "john@example.com"})
        """
        columns = ', '.join(data.keys())
        placeholders = ', '.join(['?' for _ in data])
        query = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"

        with self.cursor(commit=commit) as cursor:
            cursor.execute(query, tuple(data.values()))
            return cursor.lastrowid

    def update(self, table: str, data: Dict[str, Any], where: str,
               where_params: Union[tuple, None] = None, commit: bool = True) -> int:
        """
        Update rows in a table

        Args:
            table: Table name
            data: Dictionary of column-value pairs to update
            where: WHERE clause
            where_params: Parameters for WHERE clause
            commit: Whether to commit the transaction

        Returns:
            int: Number of rows affected

        Example:
            count = db.update("users", {"name": "Jane"}, "id = ?", (1,))
        """
        set_clause = ', '.join([f"{key} = ?" for key in data.keys()])
        query = f"UPDATE {table} SET {set_clause} WHERE {where}"
        params = tuple(data.values()) + (tuple(where_params) if where_params else ())

        with self.cursor(commit=commit) as cursor:
            cursor.execute(query, params)
            return cursor.rowcount

    def delete(self, table: str, where: str, where_params: Union[tuple, None] = None,
               commit: bool = True) -> int:
        """
        Delete rows from a table

        Args:
            table: Table name
            where: WHERE clause
            where_params: Parameters for WHERE clause
            commit: Whether to commit the transaction

        Returns:
            int: Number of rows affected

        Example:
            count = db.delete("users", "id = ?", (1,))
        """
        query = f"DELETE FROM {table} WHERE {where}"

        with self.cursor(commit=commit) as cursor:
            cursor.execute(query, where_params or ())
            return cursor.rowcount

    def table_exists(self, table_name: str) -> bool:
        """
        Check if a table exists in the database

        Args:
            table_name: Name of the table to check

        Returns:
            bool: True if table exists
        """
        query = """
            SELECT COUNT(*) FROM sqlite_master
            WHERE type = 'table' AND name = ?
        """
        return self.fetch_value(query, (table_name,)) > 0

    def get_table_info(self, table_name: str) -> List[sqlite3.Row]:
        """
        Get information about table structure

        Args:
            table_name: Name of the table

        Returns:
            List[sqlite3.Row]: Table structure information
        """
        return self.fetch_all(f"PRAGMA table_info({table_name})")


# Global instance for convenience
DB = DatabaseManager(DB_IMOBILE_FILE)
DBTEST = DatabaseManager(DBTEST_IMOBILE_FILE)

# Alternative: Factory function for creating multiple database instances
def create_database_manager(db_path: str = 'db.db', **kwargs) -> DatabaseManager:
    """Factory function to create DatabaseManager instances"""
    return DatabaseManager(db_path, **kwargs)


def clear_stock_relate_data(user_id: int = 1):
    """Remove stock relate data from the database for a specific user."""
    with DB.cursor() as cursor:
        cursor.execute("DELETE FROM smart_orders WHERE user_id = ?", (user_id,))
        deleted_rows = cursor.rowcount
        logger.info(f"Removed {deleted_rows} test smart orders from the database.")
        cursor.execute("DELETE FROM holding_stocks WHERE user_id = ?", (user_id,))
        deleted_rows = cursor.rowcount
        logger.info(f"Removed {deleted_rows} test holding stocks from the database. for user_id={user_id}")
        cursor.execute("DELETE FROM transactions WHERE user_id = ?", (user_id,))
        deleted_rows = cursor.rowcount
        logger.info(f"Removed {deleted_rows} test transactions from the database. for user_id={user_id}")


def backup_reinit_db():
    """Reinitialize the database schema."""
    if DB_IMOBILE_FILE and os.path.exists(DB_IMOBILE_FILE):
        os.system(f"mv {DB_IMOBILE_FILE} {DB_IMOBILE_FILE}.bak")
        logger.info(f"Backed up existing database file: {DB_IMOBILE_FILE} with .bak suffix.")
    # Recreate empty database
    os.system(f'sqlite3 {DB_IMOBILE_FILE} < {DB_IMOBILE_SQL}')
    logger.info(f"Reinitialized database from SQL schema: {DB_IMOBILE_SQL}")

def restore_db():
    """Restore the database from backup."""
    if DB_IMOBILE_FILE and os.path.exists(DB_IMOBILE_FILE + '.bak'):
        os.system(f"mv {DB_IMOBILE_FILE}.bak {DB_IMOBILE_FILE}")
        logger.info(f"Restored database from backup: {DB_IMOBILE_FILE}.bak to {DB_IMOBILE_FILE}")
    else:
        logger.warning(f"No backup file found to restore: {DB_IMOBILE_FILE}.bak")



if __name__ == '__main__':
    with DB.cursor() as cursor:
        result = cursor.execute("select * from app_config where user_id=1 and market='A-shares'").fetchone()
    print(dict(result))
    sys.exit(0)

    # Insert a user
    user_id = db.insert("users", {
        "name": "John Doe",
        "email": "john@example.com"
    })
    print(f"Inserted user with ID: {user_id}")

    # Fetch one user
    user = db.fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if user:
        print(f"User: {dict(user)}")

    # Fetch all users
    users = db.fetch_all("SELECT * FROM users")
    print(f"Total users: {len(users)}")

    # Update user
    affected = db.update("users", {"name": "Jane Doe"}, "id = ?", (user_id,))
    print(f"Updated {affected} users")

    # Fetch single value
    count = db.fetch_value("SELECT COUNT(*) FROM users")
    print(f"Total user count: {count}")

    # Complex queries
    with DB.cursor() as cursor:
        cursor.execute("""
            SELECT u.name, COUNT(*) as post_count
            FROM users u
            LEFT JOIN posts p ON u.id = p.user_id
            GROUP BY u.id, u.name
            HAVING COUNT(*) > 0
        """)
        for row in cursor.fetchall():
            print(f"User {row['name']} has {row['post_count']} posts")

    # Batch operations
    users_data = [
        {"name": "User1", "email": "user1@example.com"},
        {"name": "User2", "email": "user2@example.com"},
        {"name": "User3", "email": "user3@example.com"},
    ]
    # Batch insert in transaction
    with db.connect() as conn:
        cursor = conn.cursor()
        for user_data in users_data:
            cursor.execute(
                "INSERT INTO users (name, email) VALUES (?, ?)",
                (user_data["name"], user_data["email"])
            )
        print(f"Inserted {len(users_data)} users in one transaction")
