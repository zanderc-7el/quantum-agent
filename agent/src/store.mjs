import {DatabaseSync} from 'node:sqlite';
import fs from 'node:fs';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
export class Store {
  constructor(file){if(file!==':memory:')fs.mkdirSync(path.dirname(file),{recursive:true});this.db=new DatabaseSync(file);this.db.exec(`PRAGMA journal_mode=WAL; CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,created_at TEXT NOT NULL); CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,created_at TEXT NOT NULL); CREATE TABLE IF NOT EXISTS model_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,event_type TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);`);}
  session(id){if(id!==undefined&&(!/^[a-zA-Z0-9_-]{1,120}$/.test(id)))throw new Error('invalid_session_id');id ||=randomUUID();this.db.prepare('INSERT OR IGNORE INTO sessions VALUES (?,?)').run(id,new Date().toISOString());return id;}
  message(id,role,content){this.db.prepare('INSERT INTO messages VALUES (?,?,?,?,?)').run(randomUUID(),id,role,content,new Date().toISOString());}
  history(id){return this.db.prepare('SELECT role,content FROM (SELECT rowid,role,content FROM messages WHERE session_id=? ORDER BY rowid DESC LIMIT 20) ORDER BY rowid').all(id);}
  event(id,type,payload){return Number(this.db.prepare('INSERT INTO model_events(session_id,event_type,payload,created_at) VALUES(?,?,?,?)').run(id,type,JSON.stringify(payload),new Date().toISOString()).lastInsertRowid);}
  events(id,after=0){return this.db.prepare('SELECT * FROM model_events WHERE session_id=? AND seq>? ORDER BY seq LIMIT 1000').all(id,after).map(r=>({...r,payload:JSON.parse(r.payload)}));}
  close(){this.db.close();}
}
