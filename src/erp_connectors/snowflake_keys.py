"""Generate an encrypted RSA key pair locally; never overwrite existing files."""

import argparse
import getpass
import warnings
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def main():
    parser = argparse.ArgumentParser(description="Generate a local Snowflake service-user RSA key pair")
    parser.add_argument("--directory", type=Path, default=Path(".local-connections/snowflake-keys"))
    args = parser.parse_args()
    private_path, public_path = args.directory / "rsa_key.p8", args.directory / "rsa_key.pub"
    if private_path.exists() or public_path.exists():
        raise SystemExit("Key files already exist; choose another directory for rotation")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        password = getpass.getpass("New private-key passphrase (hidden): ")
        repeat = getpass.getpass("Repeat passphrase (hidden): ")
    if len(password) < 12 or password != repeat:
        raise SystemExit("Use matching passphrases with at least 12 characters")
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    args.directory.mkdir(parents=True, exist_ok=True)
    with private_path.open("xb") as output:
        output.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.BestAvailableEncryption(password.encode())))
    with public_path.open("xb") as output:
        output.write(key.public_key().public_bytes(serialization.Encoding.PEM,
                                                  serialization.PublicFormat.SubjectPublicKeyInfo))
    print(f"Encrypted private key: {private_path.resolve()}")
    print(f"Public key: {public_path.resolve()}")
    print("Register only the public key in Snowflake. Keep the private key and passphrase local.")


if __name__ == "__main__":
    main()
