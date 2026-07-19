import sqlite3
import time
import threading
from pathlib import Path
from typing import Optional, Tuple

class AudioCache:
    """
    SQLite-based audio cache with High/Low Watermark LRU eviction.
    Implements strict thread-locking to prevent "Database is Locked" collisions.
    """

    def __init__(self, db_path: Path, max_size_mb: float = 200.0):
        self.db_path = db_path
        self.max_size_mb = max_size_mb
        self.lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        """Create database schema if it does not exist, safely locked."""
        with self.lock:
            try:
                # timeout=15.0 ensures threads wait in line politely during heavy TTS generation
                conn = sqlite3.connect(str(self.db_path), timeout=15.0)
                
                # WAL mode prevents read/write blocking
                conn.execute('PRAGMA journal_mode=WAL;')
                conn.execute('PRAGMA synchronous=NORMAL;')
                
                cursor = conn.cursor()
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS audio_cache (
                        cache_key TEXT PRIMARY KEY,
                        audio_data BLOB NOT NULL,
                        size_bytes INTEGER NOT NULL,
                        created_at REAL NOT NULL,
                        accessed_at REAL NOT NULL
                    )
                    """
                )
                
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_accessed_at ON audio_cache(accessed_at)")
                
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"[CACHE WARNING] Database initialization failed: {e}")

    def put(self, cache_key: str, audio_bytes: bytes):
        """
        Safely store audio data in the cache.
        Includes a Garbage Guard to reject broken files and triggers watermark cleanup.
        """
        # 1. Garbage Guard: Reject corrupt or purely empty audio.
        # Anything under 2048 bytes is mathematically a crashed or empty generation.
        if len(audio_bytes) < 2048:
            print(f"[CACHE GUARD] Audio too small ({len(audio_bytes)} bytes). Refusing to save.")
            return

        size_bytes = len(audio_bytes)
        current_time = time.time()

        # 2. Database Write with Lock
        with self.lock:
            try:
                conn = sqlite3.connect(str(self.db_path), timeout=15.0)
                cursor = conn.cursor()
                
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO audio_cache 
                    (cache_key, audio_data, size_bytes, created_at, accessed_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (cache_key, audio_bytes, size_bytes, current_time, current_time),
                )
                
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"[CACHE WARNING] Failed to write to database: {e}")

        # 3. Enforce limits outside of the lock to prevent deadlocks
        self._cleanup_if_needed()

    def get(self, cache_key: str) -> Optional[bytes]:
        """Retrieve audio data and update its access time for LRU tracking."""
        with self.lock:
            try:
                conn = sqlite3.connect(str(self.db_path), timeout=15.0)
                cursor = conn.cursor()

                cursor.execute("SELECT audio_data FROM audio_cache WHERE cache_key = ?", (cache_key,))
                row = cursor.fetchone()

                if row is not None:
                    # Update access time to prevent eviction
                    cursor.execute(
                        "UPDATE audio_cache SET accessed_at = ? WHERE cache_key = ?",
                        (time.time(), cache_key),
                    )
                    conn.commit()
                    conn.close()
                    
                    return row[0]

                conn.close()
                return None
                
            except Exception as e:
                print(f"[CACHE WARNING] Failed to read from database: {e}")
                return None

    def _cleanup_if_needed(self):
        """
        High/Low Watermark Cleanup logic. 
        If max size is hit, purges the oldest files down to 75% capacity to prevent infinite loops.
        """
        total_size_mb = self.get_size_mb()

        if total_size_mb <= self.max_size_mb:
            return

        # Calculate Low Watermark (75% of max size)
        target_size_mb = self.max_size_mb * 0.75
        target_size_bytes = int(target_size_mb * 1024 * 1024)

        with self.lock:
            try:
                conn = sqlite3.connect(str(self.db_path), timeout=15.0)
                cursor = conn.cursor()

                # Fetch all entries sorted by oldest access time
                cursor.execute("SELECT cache_key, size_bytes FROM audio_cache ORDER BY accessed_at ASC")
                entries = cursor.fetchall()

                current_size_bytes = sum(entry[1] for entry in entries)

                for cache_key, size_bytes in entries:
                    if current_size_bytes <= target_size_bytes:
                        break 

                    cursor.execute("DELETE FROM audio_cache WHERE cache_key = ?", (cache_key,))
                    current_size_bytes -= size_bytes

                conn.commit()

                # Force SQLite to physically shrink the .db file on the hard drive
                cursor.execute("VACUUM")
                conn.commit()
                conn.close()

                print(f"[CACHE LIMIT] Auto clean {target_size_mb:.2f}MB")
                
            except Exception as e:
                print(f"[CACHE CLEANUP ERROR] Cleanup failed: {e}")

    def get_size_mb(self) -> float:
        """Calculate total cache size."""
        with self.lock:
            try:
                conn = sqlite3.connect(str(self.db_path), timeout=15.0)
                cursor = conn.cursor()
                
                cursor.execute("SELECT SUM(size_bytes) FROM audio_cache")
                result = cursor.fetchone()
                total_bytes = result[0] if result[0] is not None else 0
                
                conn.close()
                return total_bytes / (1024 * 1024)
            except Exception:
                return 0.0

    def get_count(self) -> int:
        """Count total cached entries."""
        with self.lock:
            try:
                conn = sqlite3.connect(str(self.db_path), timeout=15.0)
                cursor = conn.cursor()
                
                cursor.execute("SELECT COUNT(*) FROM audio_cache")
                result = cursor.fetchone()
                count = result[0] if result[0] is not None else 0
                
                conn.close()
                return count
            except Exception:
                return 0

    def clear_all(self) -> Tuple[int, float]:
        """Wipe the entire database and instantly reclaim disk space by deleting the file."""
        import os
        with self.lock:
            try:
                # 1. Grab stats before destroying the database
                conn = sqlite3.connect(str(self.db_path), timeout=15.0)
                cursor = conn.cursor()
                
                cursor.execute("SELECT COUNT(*), SUM(size_bytes) FROM audio_cache")
                row = cursor.fetchone()
                
                count = row[0] if row[0] is not None else 0
                size_mb = (row[1] / (1024 * 1024)) if row[1] is not None else 0.0
                conn.close()

                # 2. 🌟 INSTANT NUKE: Physically delete the database and WAL temporary files
                # This is infinitely faster and safer than using DELETE FROM or VACUUM
                for ext in ["", "-wal", "-shm"]:
                    target_file = Path(str(self.db_path) + ext)
                    if target_file.exists():
                        try:
                            target_file.unlink()
                        except Exception as e:
                            print(f"[CACHE WARNING] Could not delete {target_file.name}: {e}")

                # 3. 🌟 SILENT REBUILD: Create a fresh, completely empty database
                # (We do this inline to prevent thread-lock deadlocks)
                conn = sqlite3.connect(str(self.db_path), timeout=15.0)
                conn.execute('PRAGMA journal_mode=WAL;')
                conn.execute('PRAGMA synchronous=NORMAL;')
                cursor = conn.cursor()
                
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS audio_cache (
                        cache_key TEXT PRIMARY KEY,
                        audio_data BLOB NOT NULL,
                        size_bytes INTEGER NOT NULL,
                        created_at REAL NOT NULL,
                        accessed_at REAL NOT NULL
                    )
                    """
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_accessed_at ON audio_cache(accessed_at)")
                conn.commit()
                conn.close()

                print(f"\n[CACHE WIPE] Total Wipe Successful. Freed {size_mb:.2f}MB from {count} files.\n")
                return count, size_mb
                
            except Exception as e:
                print(f"[CACHE ERROR] Clear all failed: {e}")
                return 0, 0.0