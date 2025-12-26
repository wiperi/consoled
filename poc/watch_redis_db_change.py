#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import sys
import time
from typing import Dict, Any

import redis


CONFIG_DB_ID = 4  # SONiC CONFIG_DB 通常是 db=4


def decode_dict(d: Dict[bytes, bytes]) -> Dict[str, str]:
    out = {}
    for k, v in d.items():
        out[k.decode("utf-8", "replace")] = v.decode("utf-8", "replace")
    return out


def ensure_notifications(r: redis.Redis, enable: bool) -> None:
    cur = r.config_get("notify-keyspace-events").get("notify-keyspace-events", "")
    if cur:
        print(f"[info] notify-keyspace-events already set: {cur!r}")
        return

    msg = (
        "[warn] notify-keyspace-events is empty/disabled.\n"
        "       Without it, you won't receive change notifications.\n"
        "       You can enable it via:\n"
        "         redis-cli -n 4 CONFIG SET notify-keyspace-events Khg\n"
        "       (K: Keyspace events, h: hash events, g: generic events)\n"
    )
    print(msg)

    if enable:
        # Khg 足够覆盖：hset/hdel + del/expire 等
        r.config_set("notify-keyspace-events", "Khg")
        newv = r.config_get("notify-keyspace-events").get("notify-keyspace-events", "")
        print(f"[info] enabled notify-keyspace-events: {newv!r}")


def print_key_snapshot(r: redis.Redis, key: str) -> None:
    t = time.strftime("%Y-%m-%d %H:%M:%S")
    # CONFIG_DB 的 table entry 一般是 hash
    if r.type(key) == b"hash":
        data = decode_dict(r.hgetall(key))
        print(f"  [{t}] HGETALL {key} -> {data}")
    else:
        # 也可能是 string/set 等（少见），这里简单打印 type
        print(f"  [{t}] key {key} type={r.type(key)}")


def main():
    ap = argparse.ArgumentParser(
        description="Subscribe CONFIG_DB changes via Redis keyspace notifications (SONiC)."
    )
    ap.add_argument("--host", default="127.0.0.1", help="Redis host (default: 127.0.0.1)")
    ap.add_argument("--port", type=int, default=6379, help="Redis port (default: 6379)")
    ap.add_argument("--db", type=int, default=CONFIG_DB_ID, help="Redis DB id (default: 4 for CONFIG_DB)")
    ap.add_argument(
        "--pattern",
        default="*",
        help="Key pattern to watch (default: *). Example: 'PORT|*' or 'VLAN|Vlan*'",
    )
    ap.add_argument(
        "--enable",
        action="store_true",
        help="Try to enable notify-keyspace-events automatically (CONFIG SET).",
    )
    args = ap.parse_args()

    r = redis.Redis(host=args.host, port=args.port, db=args.db, decode_responses=False)
    try:
        r.ping()
    except Exception as e:
        print(f"[error] cannot connect redis: {e}", file=sys.stderr)
        return 2

    ensure_notifications(r, enable=args.enable)

    # Keyspace channel pattern:
    #   __keyspace@<db>__:<key>
    p = r.pubsub(ignore_subscribe_messages=True)
    chan_pat = f"__keyspace@{args.db}__:{args.pattern}"
    p.psubscribe(chan_pat)

    print(f"[info] subscribed keyspace pattern: {chan_pat}")
    print("[info] waiting for events... (Ctrl+C to quit)")
    print("       tip: change config via sonic-db-cli CONFIG_DB or redis-cli -n 4 ...")

    try:
        for msg in p.listen():
            # msg example:
            # {'type': 'pmessage', 'pattern': b'__keyspace@4__:*',
            #  'channel': b'__keyspace@4__:PORT|Ethernet0', 'data': b'hset'}
            if msg.get("type") != "pmessage":
                continue

            channel = msg["channel"].decode("utf-8", "replace")
            event = msg["data"].decode("utf-8", "replace")
            key = channel.split(":", 1)[1] if ":" in channel else channel

            t = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{t}] event={event} key={key}")

            # 对于 del 事件，key 可能已经不存在
            if event in ("del", "expired", "evicted"):
                exists = bool(r.exists(key))
                print(f"  [{t}] exists={exists}")
                if exists:
                    print_key_snapshot(r, key)
            else:
                print_key_snapshot(r, key)

    except KeyboardInterrupt:
        print("\n[info] bye")
        return 0
    finally:
        try:
            p.close()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
