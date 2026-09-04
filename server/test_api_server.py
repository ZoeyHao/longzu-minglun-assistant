#!/usr/bin/env python3
from __future__ import annotations

import unittest

from api_server import MAX_HISTORY, append_history, owner_hash


def record(index: int, owner: str) -> dict:
    return {
        "id": index,
        "owner_hash": owner,
        "nick": "测试",
        "ts": index,
        "payload": {},
        "result": {},
    }


class TestHistoryIsolation(unittest.TestCase):
    def test_owner_token_is_validated_and_hashed(self):
        self.assertIsNone(owner_hash("short"))
        self.assertEqual(owner_hash("a" * 32), owner_hash("a" * 32))
        self.assertNotEqual(owner_hash("a" * 32), owner_hash("b" * 32))

    def test_history_limit_is_per_owner(self):
        records = [record(i, "owner-a") for i in range(MAX_HISTORY + 4)]
        records.append(record(900, "owner-b"))
        merged = append_history(records, record(999, "owner-a"))
        mine = [item for item in merged if item["owner_hash"] == "owner-a"]
        self.assertEqual(len(mine), MAX_HISTORY)
        self.assertEqual(mine[-1]["id"], 999)
        self.assertTrue(any(item["owner_hash"] == "owner-b" for item in merged))


if __name__ == "__main__":
    unittest.main(verbosity=2)
