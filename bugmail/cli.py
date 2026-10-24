"""Command line: check one address, clean a list, diagnose your network, or start the web UI."""

import argparse
import socket
import sys

from . import __version__
from .tables import read_table, write_table, summarize
from .verifier import Verifier

PROBE_HOST = "gmail-smtp-in.l.google.com"


def _verifier(args) -> Verifier:
    extra = set()
    if args.disposable_file:
        with open(args.disposable_file, encoding="utf-8") as fh:
            extra = {line.strip().lower() for line in fh if line.strip() and not line.startswith("#")}
    return Verifier(smtp=not args.no_smtp, helo=args.helo, sender=args.sender, timeout=args.timeout,
                    min_interval=args.interval, allow_private=args.allow_private, extra_disposable=extra)


def cmd_check(args):
    res = _verifier(args).check(args.email)
    print(f"{res.status.upper():8} {res.reason:20} {res.email}" + (f"  -> did you mean {res.suggestion}?" if res.suggestion else ""))
    return 0 if res.status == "valid" else 1


def cmd_file(args):
    with open(args.input, encoding="utf-8-sig", newline="") as fh:
        header, rows, idx = read_table(fh.read(), args.column)
    emails = [r[idx] for r in rows if len(r) > idx]
    done = {"n": 0}
    total = len(set(e.strip().lower() for e in emails if e.strip()))

    def progress(_res):
        done["n"] += 1
        if done["n"] % 25 == 0 or done["n"] == total:
            print(f"\r{done['n']}/{total}", end="", file=sys.stderr, flush=True)

    results = _verifier(args).check_many(emails, workers=args.workers, on_result=progress)
    print(file=sys.stderr)
    with open(args.output, "w", encoding="utf-8", newline="") as out:
        write_table(header, rows, idx, results, out)
    counts = summarize(results)
    print(f"{len(results)} unique addresses -> {args.output}")
    print("  ".join(f"{k}: {v}" for k, v in counts.items()))
    return 0


def cmd_doctor(_args):
    print(f"bugmail {__version__}")
    try:
        socket.create_connection((PROBE_HOST, 25), timeout=8).close()
        print(f"OK    outbound port 25 is open ({PROBE_HOST})")
        print("      SMTP verification should work from this network.")
        return 0
    except OSError as exc:
        print(f"FAIL  cannot reach {PROBE_HOST}:25 ({exc})")
        print("      Many ISPs and clouds block outbound port 25. Run bugmail from a server where it is open,")
        print("      or use --no-smtp to check only syntax, domains and mail servers.")
        return 2


def cmd_serve(args):
    from .web import create_app

    app = create_app(_verifier(args), workers=args.workers)
    app.run(host=args.host, port=args.port)
    return 0


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--no-smtp", action="store_true", help="skip the SMTP handshake (syntax + DNS only)")
    common.add_argument("--helo", help="hostname announced to mail servers (default: this machine's FQDN)")
    common.add_argument("--sender", default="", help="MAIL FROM address (default: empty null sender)")
    common.add_argument("--timeout", type=float, default=10.0, help="seconds per SMTP connection")
    common.add_argument("--interval", type=float, default=1.0, help="min seconds between connections to the same mail server")
    common.add_argument("--workers", type=int, default=8, help="parallel checks")
    common.add_argument("--allow-private", action="store_true", help="allow mail servers on private/LAN addresses")
    common.add_argument("--disposable-file", help="text file with extra disposable domains, one per line")

    p = argparse.ArgumentParser(prog="bugmail", description="E-mail list cleaner.")
    p.add_argument("--version", action="version", version=f"bugmail {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("check", parents=[common], help="check one address")
    s.add_argument("email")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("file", parents=[common], help="clean a CSV/TXT list")
    s.add_argument("input")
    s.add_argument("-o", "--output", required=True)
    s.add_argument("--column", help="name of the column with the addresses (auto-detected by default)")
    s.set_defaults(func=cmd_file)

    s = sub.add_parser("doctor", help="check whether this network can reach SMTP servers")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("serve", parents=[common], help="start the web interface")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=5000)
    s.set_defaults(func=cmd_serve)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
