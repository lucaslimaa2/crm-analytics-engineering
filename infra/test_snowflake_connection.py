"""Verify Snowflake RBAC is correctly enforced (Phase 4 verification).

Connects as each of the three service users in turn and runs a battery of
permitted/forbidden statements. Each should either succeed or fail as
designed:

  LOADER       can: USE/CREATE in RAW
               cannot: USE STAGING, USE MARTS

  TRANSFORMER  can: USE RAW, USE/CREATE in STAGING + MARTS
               cannot: CREATE in RAW

  REPORTER     can: USE MARTS
               cannot: USE RAW, USE STAGING, CREATE anywhere

Exit code 0 if every expectation holds, 1 otherwise.

Usage:
    python infra/test_snowflake_connection.py
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

import snowflake.connector
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

ACCOUNT   = os.environ["SNOWFLAKE_ACCOUNT"]
WAREHOUSE = os.environ["SNOWFLAKE_WAREHOUSE"]
DATABASE  = os.environ["SNOWFLAKE_DATABASE"]

USERS = {
    "LOADER":      (os.environ["SNOWFLAKE_USER_LOADER"],      os.environ["SNOWFLAKE_PASSWORD_LOADER"]),
    "TRANSFORMER": (os.environ["SNOWFLAKE_USER_TRANSFORMER"], os.environ["SNOWFLAKE_PASSWORD_TRANSFORMER"]),
    "REPORTER":    (os.environ["SNOWFLAKE_USER_REPORTER"],    os.environ["SNOWFLAKE_PASSWORD_REPORTER"]),
}

failures = 0


@contextmanager
def connect(user: str, password: str):
    conn = snowflake.connector.connect(
        account=ACCOUNT,
        user=user,
        password=password,
        warehouse=WAREHOUSE,
        database=DATABASE,
    )
    try:
        yield conn
    finally:
        conn.close()


def assert_can(cur, sql: str, label: str) -> None:
    global failures
    try:
        cur.execute(sql)
        print(f"  [PASS] {label}")
    except Exception as e:
        failures += 1
        print(f"  [FAIL] {label}: {type(e).__name__}: {str(e).splitlines()[0]}")


def assert_cannot(cur, sql: str, label: str) -> None:
    global failures
    try:
        cur.execute(sql)
        failures += 1
        print(f"  [FAIL] {label} succeeded but should have been denied")
    except Exception:
        print(f"  [PASS] {label} correctly denied")


def test_loader() -> None:
    print("\nLOADER role (writes to RAW, blocked everywhere else)")
    user, pwd = USERS["LOADER"]
    with connect(user, pwd) as conn:
        cur = conn.cursor()
        assert_can(cur,    "SELECT CURRENT_ROLE()",                                "auth + role assumption")
        assert_can(cur,    "USE SCHEMA REVOPS.RAW",                                "USE schema RAW")
        assert_can(cur,    "CREATE OR REPLACE TABLE REVOPS.RAW.RBAC_TEST (x INT)", "CREATE TABLE in RAW")
        assert_can(cur,    "DROP TABLE REVOPS.RAW.RBAC_TEST",                      "DROP own table in RAW")
        assert_cannot(cur, "USE SCHEMA REVOPS.STAGING",                            "USE schema STAGING")
        assert_cannot(cur, "USE SCHEMA REVOPS.MARTS",                              "USE schema MARTS")


def test_transformer() -> None:
    print("\nTRANSFORMER role (reads RAW, writes STAGING + MARTS, blocked from writing RAW)")
    user, pwd = USERS["TRANSFORMER"]
    with connect(user, pwd) as conn:
        cur = conn.cursor()
        assert_can(cur,    "USE SCHEMA REVOPS.RAW",                                       "USE schema RAW (read)")
        assert_can(cur,    "USE SCHEMA REVOPS.STAGING",                                   "USE schema STAGING")
        assert_can(cur,    "USE SCHEMA REVOPS.MARTS",                                     "USE schema MARTS")
        assert_can(cur,    "CREATE OR REPLACE TABLE REVOPS.STAGING.RBAC_TEST (x INT)",    "CREATE TABLE in STAGING")
        assert_can(cur,    "DROP TABLE REVOPS.STAGING.RBAC_TEST",                         "DROP own table in STAGING")
        assert_can(cur,    "CREATE OR REPLACE TABLE REVOPS.MARTS.RBAC_TEST (x INT)",      "CREATE TABLE in MARTS")
        assert_can(cur,    "DROP TABLE REVOPS.MARTS.RBAC_TEST",                           "DROP own table in MARTS")
        assert_cannot(cur, "CREATE OR REPLACE TABLE REVOPS.RAW.RBAC_TEST_T (x INT)",      "CREATE TABLE in RAW")


def test_reporter() -> None:
    print("\nREPORTER role (reads MARTS only, blocked from RAW + STAGING + any write)")
    user, pwd = USERS["REPORTER"]
    with connect(user, pwd) as conn:
        cur = conn.cursor()
        assert_can(cur,    "USE SCHEMA REVOPS.MARTS",                                     "USE schema MARTS")
        assert_cannot(cur, "USE SCHEMA REVOPS.RAW",                                       "USE schema RAW")
        assert_cannot(cur, "USE SCHEMA REVOPS.STAGING",                                   "USE schema STAGING")
        assert_cannot(cur, "CREATE OR REPLACE TABLE REVOPS.MARTS.RBAC_TEST_R (x INT)",    "CREATE TABLE in MARTS")


def main() -> int:
    print(f"Snowflake account: {ACCOUNT}")
    print(f"Warehouse:         {WAREHOUSE}")
    print(f"Database:          {DATABASE}")

    test_loader()
    test_transformer()
    test_reporter()

    print()
    if failures:
        print(f"RBAC test: {failures} expectation(s) violated")
        return 1
    print("RBAC test: all expectations hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
