"""Command line for the DWARF 3."""
import argparse
import os
from pathlib import Path

from .files import Album
from .scope import ALBUM_ROOT, FILTERS, MOTOR_PITCH, MOTOR_ROTATION, Scope

LAT = float(os.environ.get("DWARF3_LAT", "0.0"))
LON = float(os.environ.get("DWARF3_LON", "-100.0"))
UTC_OFFSET = float(os.environ.get("DWARF3_UTC_OFFSET", "-8"))
TIMEZONE = os.environ.get("DWARF3_TZ", "America/Los_Angeles")


def session(args, fn):
    scope = Scope(args.host)
    try:
        return fn(scope)
    finally:
        scope.close()


def cmd_status(args):
    def run(s):
        print("rotation", s.position(MOTOR_ROTATION), "pitch", s.position(MOTOR_PITCH))
        print("tele darks", len(s.dark_list()), "wide darks", len(s.dark_list(wide=True)))
    session(args, run)


def cmd_darks(args):
    session(args, lambda s: [print(r) for r in s.dark_list(wide=args.wide)])


def cmd_dark(args):
    session(args, lambda s: s.start_dark(args.exposure, args.gain, args.frames, wide=args.wide))
    print("dark capture sent, watch /Astronomy/DWARF_DARK for the frames")


def cmd_prepare(args):
    def run(s):
        s.prepare(LAT, LON, UTC_OFFSET, TIMEZONE)
        s.home()
        s.point_up()
    session(args, run)


def cmd_close(args):
    session(args, lambda s: s.close_barrel())


def cmd_calibrate(args):
    def run(s):
        s.focus_infinity()
        s.calibrate()
    session(args, run)


def cmd_goto(args):
    session(args, lambda s: s.goto(args.name, args.ra, args.dec))


def cmd_capture(args):
    def run(s):
        s.set_capture(args.exposure, args.gain, args.filter)
        print("session", s.capture(args.frames, args.filter, float(args.exposure)))
    session(args, run)


def cmd_stop(args):
    session(args, lambda s: s.stop_capture())


def cmd_mirror(args):
    album = Album(args.host)
    try:
        print("copied", album.mirror(args.path, Path(args.dest)), "files")
    finally:
        album.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="dwarf3")
    parser.add_argument("--host", default=os.environ.get("DWARF3_HOST", "192.0.2.10"))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status").set_defaults(fn=cmd_status)
    p = sub.add_parser("darks")
    p.add_argument("--wide", action="store_true")
    p.set_defaults(fn=cmd_darks)
    p = sub.add_parser("dark")
    p.add_argument("--exposure", default="15", choices=["15", "30", "60"])
    p.add_argument("--gain", default="60", choices=["60"])
    p.add_argument("--frames", type=int, default=3)
    p.add_argument("--wide", action="store_true")
    p.set_defaults(fn=cmd_dark)
    sub.add_parser("prepare").set_defaults(fn=cmd_prepare)
    sub.add_parser("close").set_defaults(fn=cmd_close)
    sub.add_parser("calibrate").set_defaults(fn=cmd_calibrate)
    p = sub.add_parser("goto")
    p.add_argument("name")
    p.add_argument("ra", type=float, help="right ascension in hours")
    p.add_argument("dec", type=float, help="declination in degrees")
    p.set_defaults(fn=cmd_goto)
    p = sub.add_parser("capture")
    p.add_argument("--frames", type=int, default=5)
    p.add_argument("--exposure", default="15", choices=["15", "30", "60"])
    p.add_argument("--gain", type=int, default=60)
    p.add_argument("--filter", default="duo", choices=sorted(FILTERS))
    p.set_defaults(fn=cmd_capture)
    sub.add_parser("stop").set_defaults(fn=cmd_stop)
    p = sub.add_parser("mirror")
    p.add_argument("dest")
    p.add_argument("--path", default=ALBUM_ROOT)
    p.set_defaults(fn=cmd_mirror)

    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
