"""
tools/release_signing.py -- the owner's Ed25519 release key, by hand.

    python tools/release_signing.py generate
    python tools/release_signing.py sign BaiheStudio-Setup-0.2.0.exe
    python tools/release_signing.py verify BaiheStudio-Setup-0.2.0.exe

`generate` prints a new key pair once; nothing is written to disk. `sign`
reads the private key from the BAIHE_RELEASE_SIGNING_KEY environment
variable, or asks for it without echoing; never from the command line, which
shell history and process lists keep. `verify` checks an installer against
its `.sig` with the committed public key, exactly as the app does.
Key handling, rotation and a leaked key: docs/runbook.md, "App updates".
"""
import argparse
import getpass
import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib import release_signature as rs  # noqa: E402

SECRET_NAME = "BAIHE_RELEASE_SIGNING_KEY"
PUBLIC_KEY_FILE = ROOT / "installer" / "release_public_key.txt"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_generate(_args) -> int:
    private, public = rs.generate()
    print("WARNING: the private key below is shown once. Store it as the GitHub Actions "
          f"secret {SECRET_NAME} (or offline, if you sign by hand), then clear this "
          "terminal. Never commit it, paste it into an issue or chat, or put it in "
          "installer/release_public_key.txt.", file=sys.stderr)
    print("PRIVATE KEY (secret):")
    print(private)
    print("PUBLIC KEY (commit as the only line of installer/release_public_key.txt):")
    print(public)
    return 0


def _private_key() -> bytes:
    text = os.environ.get(SECRET_NAME) or getpass.getpass("Private key (not shown): ")
    return rs.parse_private_key(text)


def cmd_sign(args) -> int:
    exe = Path(args.installer)
    seed = _private_key()
    signature = rs.sign(seed, exe.name, _sha256(exe))
    sig_path = Path(str(exe) + ".sig")
    sig_path.write_text(rs.format_signature(signature), encoding="ascii", newline="\n")
    print(f"Wrote {sig_path.name}, signed by {rs.public_key_for(seed)}")
    return 0


def cmd_verify(args) -> int:
    exe = Path(args.installer)
    sig_path = Path(args.sig) if args.sig else Path(str(exe) + ".sig")
    public = rs.parse_public_key(Path(args.public_key).read_text(encoding="utf-8")
                                 [:rs.MAX_KEY_FILE_BYTES])
    if public is None:
        print("The public key file holds no key.", file=sys.stderr)
        return 1
    signature = rs.parse_signature(sig_path.read_bytes()[:1024])
    if rs.verify(public, signature, exe.name, _sha256(exe)):
        print(f"{exe.name}: signature OK")
        return 0
    print(f"{exe.name}: signature does NOT verify", file=sys.stderr)
    return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("generate", help="print a new key pair").set_defaults(func=cmd_generate)
    p = sub.add_parser("sign", help=f"write <installer>.sig with the key in ${SECRET_NAME}")
    p.add_argument("installer")
    p.set_defaults(func=cmd_sign)
    p = sub.add_parser("verify", help="check <installer> against its .sig")
    p.add_argument("installer")
    p.add_argument("--sig", help="signature file (default: <installer>.sig)")
    p.add_argument("--public-key", default=str(PUBLIC_KEY_FILE),
                   help="public key file (default: installer/release_public_key.txt)")
    p.set_defaults(func=cmd_verify)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except rs.SignatureFormatError as exc:
        print(f"Refused: {exc}.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
