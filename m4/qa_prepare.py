import hashlib
import json
import secrets
import subprocess
import tarfile
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from datetime import datetime, timedelta, timezone
import ipaddress

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/m4-step7"


def main():
    values = dict(line.split("=", 1) for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines()
                  if line.strip() and not line.startswith("#") and "=" in line)
    images = json.loads((OUT / "release/images.json").read_bytes())
    env = {key: values.get(key, "") for key in ("BOOTSTRAP_LOGIN", "BOOTSTRAP_PASSWORD", "DEEPSEEK_API_KEY",
                                              "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL")}
    env.update(M4_RELEASE=images["release"], MYSQL_SERVER_ID="71", HTTPS_BIND="0.0.0.0", HTTPS_PORT="443",
               TLS_DIR="/etc/drawing-platform/tls", CAD_STATE="/var/lib/drawing-platform/cad",
               M1_VISION_PROJECT_ID="1", M1_VISION_BUDGET_CNY="10")
    env["MYSQL_PASSWORD"] = secrets.token_urlsafe(24)
    env["MYSQL_ROOT_PASSWORD"] = secrets.token_urlsafe(24)
    (OUT / "deploy.env").write_text("\n".join(f"{key}={value}" for key, value in env.items()) + "\n", encoding="utf-8")
    certdir = OUT / "tls"
    certdir.mkdir(exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "M4 isolated QA")])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                   .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
                   .not_valid_after(now + timedelta(days=7))
                   .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                                   x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
                   .sign(key, hashes.SHA256()))
    (certdir / "cert.pem").write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    (certdir / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                    serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    command = ["docker", "compose", "-p", "drawing-step6-qa", "exec", "-T", "mysql", "sh", "-ec",
               'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqldump -uroot --single-transaction --no-tablespaces '
               '--skip-add-drop-table --hex-blob --set-gtid-purged=OFF --max-allowed-packet=256M drawings']
    result = subprocess.run(command, cwd=ROOT, capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
    (OUT / "history.sql").write_bytes(result.stdout)
    with tarfile.open(OUT / "history-pending.tar", "w") as archive:
        archive.add(ROOT / "output/m3-step6/cad/pending", arcname="pending")
    (OUT / "history-export.json").write_text(json.dumps({"command": command, "exit": result.returncode,
        "bytes": len(result.stdout), "sha256": hashlib.sha256(result.stdout).hexdigest(),
        "source_project": "drawing-step6-qa", "read_only_export": True}, indent=2), encoding="utf-8")
    print("QA environment, TLS, read-only M3 export prepared; credentials omitted")


if __name__ == "__main__":
    main()
