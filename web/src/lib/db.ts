import Database from "better-sqlite3";
import path from "node:path";

const DB_PATH = process.env.ELOSYS_DB_PATH ?? path.join(process.cwd(), "..", "elosys.db");

let _db: Database.Database | null = null;

/** Lowercase without accents ("Tributário" -> "tributario"); same rule as SQL fold(). */
export function foldText(s: string): string {
  return s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

export function db(): Database.Database {
  if (_db) return _db;
  _db = new Database(DB_PATH, { readonly: true, fileMustExist: true });
  _db.pragma("query_only = ON");
  // SQLite LIKE folds ASCII case only: searches compare fold(column) LIKE foldText(query).
  _db.function("fold", { deterministic: true }, (s: unknown) => (s == null ? null : foldText(String(s))));
  return _db;
}

const _tables = new Set<string>();
let _tablesLoaded = false;

export function hasTable(name: string): boolean {
  if (!_tablesLoaded) {
    for (const r of db().prepare("SELECT name FROM sqlite_master WHERE type = 'table'").all() as Array<{ name: string }>) {
      _tables.add(r.name);
    }
    _tablesLoaded = true;
  }
  return _tables.has(name);
}

/** Not cached: a crawler may add a column while the server runs. */
export function hasColumn(table: string, column: string): boolean {
  return db().prepare("SELECT 1 FROM pragma_table_info(?) WHERE name = ?").get(table, column) !== undefined;
}
