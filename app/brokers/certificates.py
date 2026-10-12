import os
import ssl
import tempfile
import urllib.request
from pathlib import Path

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes

ROOT_CA = Path(__file__).parent / "certs" / "russian_trusted_root_ca.pem"
LOCAL_CA = Path(__file__).resolve().parents[2] / ".runtime" / "certs" / "russian_trusted_root_ca.pem"
SOURCE_URL = "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt"
ROOT_FINGERPRINT = "d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31"


def tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=LOCAL_CA if LOCAL_CA.exists() else ROOT_CA)
    return context


def install_certificate() -> Path:
    request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "NewsTraderBot certificate repair"})
    download_context = ssl.create_default_context()
    download_context.load_verify_locations(cafile=ROOT_CA)
    with urllib.request.urlopen(request, context=download_context, timeout=15) as response:
        data = response.read(10_001)
    if len(data) > 10_000:
        raise ValueError("Слишком большой файл сертификата")
    certificate = x509.load_pem_x509_certificate(data)
    if certificate.fingerprint(hashes.SHA256()).hex() != ROOT_FINGERPRINT or certificate.issuer != certificate.subject:
        raise ValueError("Отпечаток сертификата не совпадает с проверенным")
    LOCAL_CA.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=LOCAL_CA.parent, prefix=".t_invest_ca_", suffix=".pem")
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
        os.replace(temporary, LOCAL_CA)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return LOCAL_CA


def repair_and_check(base_url: str) -> Path:
    path = install_certificate()
    response = httpx.post(
        f"{base_url}.UsersService/GetAccounts",
        json={"status": "ACCOUNT_STATUS_OPEN"},
        verify=tls_context(),
        timeout=15,
    )
    if response.status_code not in (401, 403):
        raise ValueError(f"Неожиданный ответ API без токена: HTTP {response.status_code}")
    return path
