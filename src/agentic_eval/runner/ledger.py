from __future__ import annotations

import sqlite3
from pathlib import Path

from agentic_eval.domain import AttemptResult, QuestionCase


class RunLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._create_schema()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS cases (
                case_id TEXT PRIMARY KEY,
                ordinal INTEGER NOT NULL,
                question TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                final_attempt INTEGER,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS attempts (
                case_id TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                worker_id TEXT NOT NULL,
                status TEXT NOT NULL,
                retry_class TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                duration_seconds REAL NOT NULL,
                result_path TEXT NOT NULL,
                trace_path TEXT NOT NULL,
                error TEXT,
                PRIMARY KEY (case_id, attempt),
                FOREIGN KEY (case_id) REFERENCES cases(case_id)
            );
            """
        )
        self.connection.commit()

    def register_cases(self, cases: list[QuestionCase]) -> None:
        self.connection.executemany(
            """
            INSERT OR IGNORE INTO cases(case_id, ordinal, question)
            VALUES (?, ?, ?)
            """,
            [(case.id, index, case.question) for index, case in enumerate(cases)],
        )
        self.connection.commit()

    def pending_case_ids(self) -> set[str]:
        rows = self.connection.execute(
            "SELECT case_id FROM cases WHERE status NOT IN ('success', 'completed_failure')"
        )
        return {str(row["case_id"]) for row in rows}

    def next_attempt(self, case_id: str) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(attempt), 0) AS value FROM attempts WHERE case_id = ?",
            (case_id,),
        ).fetchone()
        return int(row["value"]) + 1

    def record_attempt(
        self,
        worker_id: str,
        result: AttemptResult,
        result_path: Path,
        trace_path: Path,
        final: bool,
    ) -> None:
        retry_class = "infrastructure" if result.infrastructure_failure else "agent"
        result.retry_class = retry_class
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO attempts(
                    case_id, attempt, worker_id, status, retry_class,
                    started_at, finished_at, duration_seconds,
                    result_path, trace_path, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.case_id,
                    result.attempt,
                    worker_id,
                    result.failure_kind.value,
                    retry_class,
                    result.started_at.isoformat(),
                    result.finished_at.isoformat(),
                    result.duration_seconds,
                    str(result_path),
                    str(trace_path),
                    result.error,
                ),
            )
            if result.successful:
                status = "success"
            elif final:
                status = "completed_failure"
            else:
                status = "retrying"
            self.connection.execute(
                """
                UPDATE cases
                SET status = ?, final_attempt = ?, updated_at = CURRENT_TIMESTAMP
                WHERE case_id = ?
                """,
                (status, result.attempt if final or result.successful else None, result.case_id),
            )

    def summary(self) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT status, COUNT(*) AS count FROM cases GROUP BY status"
        )
        return {str(row["status"]): int(row["count"]) for row in rows}

    def final_rows(self) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """
                SELECT c.case_id, c.question, c.status, c.final_attempt,
                       a.worker_id, a.duration_seconds, a.result_path,
                       a.trace_path, a.error
                FROM cases c
                LEFT JOIN attempts a
                  ON a.case_id = c.case_id AND a.attempt = c.final_attempt
                ORDER BY c.ordinal
                """
            )
        )

    def close(self) -> None:
        self.connection.close()
