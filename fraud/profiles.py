"""Online behavioural profiles in Redis.

Per account:
  fp:{id}          hash   out_n, out_sum, out_sumsq, last_out_ts, in_n, h:{hour}
  fp:{id}:out      zset   outflows in the last 24h  (member "txn|amount", score ts)
  fp:{id}:in       zset   inflows in the last 24h
  fp:{id}:payees   set    accounts this account has paid
  fp:{id}:senders  set    accounts that have paid this account

`snapshot` reads everything needed to score one event in a single pipelined
round trip; `record` folds the event into the profile atomically (MULTI) and
idempotently (per-transaction marker).
"""
from __future__ import annotations

import math
from functools import cache

import redis
from django.conf import settings

from events.contracts import TransactionRequested
from fraud.features import DAY_SECONDS, DEPOSIT, HOUR_SECONDS, OUTFLOW_TYPES, TRANSFER

SEEN_TTL_SECONDS = 7 * DAY_SECONDS


def _k(account_id, suffix: str = "") -> str:
    return f"fp:{account_id}{':' + suffix if suffix else ''}"


def _amount_sum(members) -> float:
    return sum(float(m.rsplit("|", 1)[1]) for m in members)


@cache
def get_profile_redis() -> redis.Redis:
    url = settings.FRAUD["PROFILE_REDIS_URL"]
    if url is None:
        import fakeredis

        return fakeredis.FakeRedis(decode_responses=True)
    return redis.Redis.from_url(url, decode_responses=True)


class ProfileStore:
    def __init__(self, client: redis.Redis | None = None):
        self.r = client or get_profile_redis()

    def snapshot(self, event: TransactionRequested, ts: float) -> dict:
        """Raw history signals for `event` (see fraud.features.HISTORY_SIGNALS)."""
        sender, recipient = event.account_id, event.recipient_id
        hour_ago, day_ago = f"({ts - HOUR_SECONDS}", f"({ts - DAY_SECONDS}"

        pipe = self.r.pipeline(transaction=False)
        pipe.hgetall(_k(sender))
        pipe.zcount(_k(sender, "out"), hour_ago, "+inf")
        pipe.zrangebyscore(_k(sender, "out"), day_ago, "+inf")
        pipe.zrangebyscore(_k(sender, "in"), day_ago, "+inf")
        if recipient:
            pipe.sismember(_k(sender, "payees"), recipient)
            pipe.hget(_k(recipient), "in_n")
            pipe.zrangebyscore(_k(recipient, "in"), day_ago, "+inf")
            pipe.scard(_k(recipient, "senders"))
        sender_hash, out_1h, out_24h, in_24h, *recipient_data = pipe.execute()

        signals = {
            "sender_out_count": int(sender_hash.get("out_n", 0)),
            "sender_out_sum": float(sender_hash.get("out_sum", 0.0)),
            "sender_out_sumsq": float(sender_hash.get("out_sumsq", 0.0)),
            "sender_last_out_ts": float(sender_hash["last_out_ts"]) if "last_out_ts" in sender_hash else math.nan,
            "sender_out_count_1h": out_1h,
            "sender_out_sum_24h": _amount_sum(out_24h),
            "sender_in_sum_24h": _amount_sum(in_24h),
            "sender_hour_count": int(sender_hash.get(f"h:{event.occurred.hour}", 0)),
        }
        if recipient:
            is_payee, in_n, recipient_in_24h, unique_senders = recipient_data
            signals |= {
                "is_new_payee": float(not is_payee),
                "recipient_in_count": int(in_n or 0),
                "recipient_in_count_24h": len(recipient_in_24h),
                "recipient_in_sum_24h": _amount_sum(recipient_in_24h),
                "recipient_unique_senders": unique_senders,
            }
        else:
            signals |= dict.fromkeys(
                ("is_new_payee", "recipient_in_count", "recipient_in_count_24h",
                 "recipient_in_sum_24h", "recipient_unique_senders"),
                math.nan,
            )
        return signals

    def record(self, event: TransactionRequested, ts: float) -> bool:
        """Fold the event into the profiles. Returns False if already recorded."""
        if not self.r.set(f"fp:seen:{event.transaction_id}", 1, nx=True, ex=SEEN_TTL_SECONDS):
            return False

        member = f"{event.transaction_id}|{event.amount}"
        amount = float(event.amount)
        pipe = self.r.pipeline(transaction=True)

        if event.type in OUTFLOW_TYPES:
            sender = _k(event.account_id)
            pipe.hincrby(sender, "out_n", 1)
            pipe.hincrbyfloat(sender, "out_sum", amount)
            pipe.hincrbyfloat(sender, "out_sumsq", amount * amount)
            pipe.hset(sender, "last_out_ts", ts)
            pipe.hincrby(sender, f"h:{event.occurred.hour}", 1)
            self._push_window(pipe, _k(event.account_id, "out"), member, ts)

        inbound_to = event.account_id if event.type == DEPOSIT else event.recipient_id if event.type == TRANSFER else None
        if inbound_to:
            pipe.hincrby(_k(inbound_to), "in_n", 1)
            self._push_window(pipe, _k(inbound_to, "in"), member, ts)

        if event.type == TRANSFER and event.recipient_id:
            pipe.sadd(_k(event.account_id, "payees"), event.recipient_id)
            pipe.sadd(_k(event.recipient_id, "senders"), event.account_id)

        pipe.execute()
        return True

    @staticmethod
    def _push_window(pipe, key: str, member: str, ts: float) -> None:
        pipe.zadd(key, {member: ts})
        pipe.zremrangebyscore(key, "-inf", ts - DAY_SECONDS)  # keep only the 24h window

    def flush(self) -> None:
        for key in self.r.scan_iter("fp:*", count=1000):
            self.r.delete(key)
