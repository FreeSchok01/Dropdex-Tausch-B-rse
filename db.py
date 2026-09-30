import sqlite3
import hashlib

def init_db():
    """Initialisiert die SQLite-Datenbank und erstellt benötigte Tabellen."""
    conn = sqlite3.connect("dropdex.db")
    cursor = conn.cursor()
    
    # Benutzer-Tabelle (Twitch Login & Sessions)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT,
            role TEXT DEFAULT 'user'
        )
    """)
    
    # Elemente / Tausch-Daten & Freigabe-Status
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            user_id TEXT,
            status TEXT DEFAULT 'pending'
        )
    """)
    
    conn.commit()
    conn.close()

def get_approved_items():
    """Gibt alle aktuell freigegebenen Einträge zurück."""
    conn = sqlite3.connect("dropdex.db")
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, name FROM items WHERE status = 'approved'")
        rows = cursor.fetchall()
        return [{"id": r[0], "name": r[1]} for r in rows]
    except Exception as e:
        print(f"Fehler beim Abrufen: {e}")
        return []
    finally:
        conn.close()

def remove_approval(item_id):
    """Entzieht die Freigabe für einen bestimmten Datensatz (Status -> pending)."""
    conn = sqlite3.connect("dropdex.db")
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE items SET status = 'pending' WHERE id = ?", (item_id,))
        conn.commit()
        return True
    except Exception as e:
        print(f"Fehler beim Entfernen der Freigabe: {e}")
        return False
    finally:
        conn.close()

def approve_item(item_id):
    """Gibt einen Datensatz frei."""
    conn = sqlite3.connect("dropdex.db")
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE items SET status = 'approved' WHERE id = ?", (item_id,))
        conn.commit()
        return True
    except Exception as e:
        print(f"Fehler bei der Freigabe: {e}")
        return False
    finally:
        conn.close()

# Datenbank beim Laden initialisieren
init_db()
