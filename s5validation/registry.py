import json
import sqlite3
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


class Registry:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS deliveries (
          product TEXT NOT NULL, delivery_id TEXT NOT NULL, revision TEXT NOT NULL,
          uri TEXT NOT NULL, acquired_at TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'DISCOVERED',
          reference_status TEXT NOT NULL DEFAULT 'UNKNOWN', reason TEXT NOT NULL DEFAULT '',
          metrics TEXT NOT NULL DEFAULT '{}', provenance TEXT NOT NULL DEFAULT '{}',
          first_seen TEXT NOT NULL, updated_at TEXT NOT NULL,
          PRIMARY KEY(product,delivery_id,revision));
        CREATE TABLE IF NOT EXISTS attempts (
          id INTEGER PRIMARY KEY, product TEXT, delivery_id TEXT, revision TEXT,
          status TEXT, reason TEXT, at TEXT);
        CREATE TABLE IF NOT EXISTS samples (
          product TEXT, delivery_id TEXT, revision TEXT, sample_key TEXT,
          area TEXT, observed_at TEXT, evidence TEXT,
          PRIMARY KEY(product,delivery_id,revision,sample_key),
          FOREIGN KEY(product,delivery_id,revision)
            REFERENCES deliveries(product,delivery_id,revision));
        PRAGMA user_version=1;
        ''')

    def discover(self, d):
        with self.db:
            cursor = self.db.execute('''INSERT OR IGNORE INTO deliveries
                (product,delivery_id,revision,uri,acquired_at,first_seen,updated_at)
                VALUES (?,?,?,?,?,?,?)''',
                (d.product,d.delivery_id,d.revision,d.uri,d.acquired_at,now(),now()))
        return cursor.rowcount == 1

    def rows(self, product=None):
        query = 'SELECT * FROM deliveries'
        args = ()
        if product:
            query += ' WHERE product=?'
            args = (product,)
        return self.db.execute(query+' ORDER BY product,acquired_at',args).fetchall()

    def record(self, d, outcome):
        key = (d.product,d.delivery_id,d.revision)
        with self.db:
            self.db.execute('''UPDATE deliveries SET status=?,reference_status=?,reason=?,
                metrics=?,provenance=?,updated_at=?
                WHERE product=? AND delivery_id=? AND revision=?''',
                (outcome.status,outcome.reference_status,outcome.reason,
                 json.dumps(outcome.metrics,allow_nan=False),
                 json.dumps(outcome.provenance,allow_nan=False),now(),*key))
            self.db.execute('INSERT INTO attempts (product,delivery_id,revision,status,reason,at) VALUES (?,?,?,?,?,?)',
                            (*key,outcome.status,outcome.reason,now()))
            self.db.execute('DELETE FROM samples WHERE product=? AND delivery_id=? AND revision=?',key)
            if outcome.status == 'COMPLETE':
                for s in outcome.samples:
                    self.db.execute('INSERT INTO samples VALUES (?,?,?,?,?,?,?)',
                        (*key,s['sample_key'],s['area'],s['observed_at'],json.dumps(s['evidence'],allow_nan=False)))

    def samples(self, product):
        # Ignore obsolete revisions when a replacement has been discovered.
        return self.db.execute('''SELECT s.* FROM samples s JOIN deliveries d
            USING(product,delivery_id,revision) WHERE s.product=? AND d.status='COMPLETE'
            AND d.rowid=(SELECT MAX(new.rowid) FROM deliveries new
              WHERE new.product=d.product AND new.delivery_id=d.delivery_id)''',(product,)).fetchall()

    def close(self):
        self.db.close()
