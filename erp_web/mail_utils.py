# -*- coding: utf-8 -*-
"""Basit e-posta gönderimi — randevu onay, iptal, hatırlatma; webhook tetikleme."""
import json
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import current_app

from credentials_vault import get_credential

logger = logging.getLogger(__name__)

# Render worker timeout (~30s) altinda kal; SMTP/HTTP takilmasinda istek olmesin.
SMTP_TIMEOUT_SEC = 15
BREVO_HTTP_TIMEOUT_SEC = 15
BREVO_SMTP_EMAIL_URL = "https://api.brevo.com/v3/smtp/email"


def _mail_credentials():
    """SMTP kullanıcı/şifre: vault → .env fallback (get_credential zinciri).

    Vault/env hatasında (CredentialsVaultError vb.) boş döner — çağıran False alır.
    """
    try:
        user = (get_credential("mail.username") or "").strip()
        password = (get_credential("mail.password") or "").strip()
        return user, password
    except Exception as e:
        logger.warning("mail credentials failed: %s", type(e).__name__)
        return "", ""


def _mail_smtp_endpoint():
    """SMTP host/port: vault (mail.server / mail.port) → Config/env → Brevo varsayılan."""
    try:
        host = (get_credential("mail.server") or "").strip()
        port_raw = (get_credential("mail.port") or "").strip()
    except Exception as e:
        logger.warning("mail smtp endpoint creds failed: %s", type(e).__name__)
        host, port_raw = "", ""
    if not host:
        host = (current_app.config.get("MAIL_SERVER") or "smtp-relay.brevo.com").strip()
    if not port_raw:
        port_raw = str(current_app.config.get("MAIL_PORT") or 587)
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        port = 587
    return host, port


def _brevo_api_key():
    """Brevo HTTP API key: vault mail.brevo_api_key → env BREVO_API_KEY."""
    try:
        return (get_credential("mail.brevo_api_key") or "").strip()
    except Exception as e:
        logger.warning("brevo api key failed: %s", type(e).__name__)
        return ""


def _mail_sender_email():
    """From adresi: MAIL_DEFAULT_SENDER → SMTP login → boş."""
    default_sender = (current_app.config.get("MAIL_DEFAULT_SENDER") or "").strip()
    if default_sender:
        return default_sender
    user, _ = _mail_credentials()
    return (user or "").strip()


def _send_mail_brevo_http(to_email, subject, body_text, body_html, api_key):
    """Brevo Transactional HTTP API (443). Başarı True, aksi False — exception yutulur."""
    try:
        import requests

        sender_email = _mail_sender_email()
        if not sender_email or "@" not in sender_email:
            logger.warning("brevo http skipped: missing sender")
            return False
        sender_name = (current_app.config.get("MAIL_SENDER_NAME") or "Payafin").strip() or "Payafin"
        payload = {
            "sender": {"name": sender_name, "email": sender_email},
            "to": [{"email": to_email}],
            "subject": subject or "",
            "textContent": body_text or "",
        }
        if body_html:
            payload["htmlContent"] = body_html
        timeout = int(
            current_app.config.get("MAIL_HTTP_TIMEOUT_SEC", BREVO_HTTP_TIMEOUT_SEC)
            or BREVO_HTTP_TIMEOUT_SEC
        )
        resp = requests.post(
            BREVO_SMTP_EMAIL_URL,
            headers={
                "api-key": api_key,
                "accept": "application/json",
                "content-type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
        if 200 <= resp.status_code < 300:
            return True
        logger.warning(
            "brevo http failed status=%s",
            getattr(resp, "status_code", "?"),
        )
        return False
    except Exception as e:
        logger.warning("brevo http error: %s", type(e).__name__)
        return False


def _send_mail_smtp(to_email, subject, body_text, body_html=None):
    """Eski SMTP yolu (geriye dönük uyumluluk)."""
    user, password = _mail_credentials()
    if not (user and password):
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    default_sender = (current_app.config.get("MAIL_DEFAULT_SENDER") or "").strip()
    msg["From"] = default_sender or user or "noreply@example.com"
    msg["To"] = to_email
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    if body_html:
        msg.attach(MIMEText(body_html, "html", "utf-8"))
    host, port = _mail_smtp_endpoint()
    timeout = int(
        current_app.config.get("MAIL_SMTP_TIMEOUT_SEC", SMTP_TIMEOUT_SEC)
        or SMTP_TIMEOUT_SEC
    )
    with smtplib.SMTP(host, port, timeout=timeout) as s:
        if current_app.config.get("MAIL_USE_TLS"):
            s.starttls()
        s.login(user, password)
        s.sendmail(msg["From"], to_email, msg.as_string())
    return True


def send_mail(to_email, subject, body_text, body_html=None):
    """Tek alıcıya e-posta gönder.

    1) Brevo HTTP API (mail.brevo_api_key) — 443
    2) Yoksa / başarısızsa SMTP (mail.username / mail.password)

    Her türlü hata (vault, timeout, SMTP/HTTP) → False; asla exception fırlatmaz.
    """
    try:
        if not to_email:
            return False
        api_key = _brevo_api_key()
        if api_key:
            if _send_mail_brevo_http(
                to_email, subject, body_text, body_html, api_key
            ):
                return True
            logger.warning("brevo http failed; falling back to smtp")
        return _send_mail_smtp(to_email, subject, body_text, body_html)
    except Exception as e:
        try:
            if current_app.debug:
                print("send_mail error:", type(e).__name__)
        except Exception:
            pass
        logger.warning("send_mail failed: %s", type(e).__name__)
        return False


def send_randevu_onay(to_email, musteri_adi, oda_adi, baslangic_str, bitis_str, randevu_id=None):
    """Randevu oluşturulduğunda onay e-postası."""
    app_url = current_app.config.get("APP_URL", "").rstrip("/")
    subject = "Randevu Onayı"
    text = f"Merhaba {musteri_adi},\n\nRandevunuz oluşturuldu.\nOda: {oda_adi}\nTarih/Saat: {baslangic_str} – {bitis_str}\n\n"
    if randevu_id and app_url:
        text += f"İptal için: {app_url}/randevu/iptal/{randevu_id}\n\n"
    text += "Bizi tercih ettiğiniz için teşekkürler."
    return send_mail(to_email, subject, text)


def send_randevu_iptal(to_email, musteri_adi, oda_adi, baslangic_str, bitis_str):
    """Randevu iptal edildiğinde bilgilendirme e-postası."""
    subject = "Randevu İptali"
    text = f"Merhaba {musteri_adi},\n\nRandevunuz iptal edilmiştir.\nOda: {oda_adi}\nTarih/Saat: {baslangic_str} – {bitis_str}\n\nYeni randevu almak için bizimle iletişime geçebilirsiniz."
    return send_mail(to_email, subject, text)


def send_randevu_hatirlatma(to_email, musteri_adi, oda_adi, baslangic_str, bitis_str):
    """Randevu öncesi hatırlatma e-postası (cron ile gönderilir)."""
    subject = "Randevu Hatırlatması"
    text = f"Merhaba {musteri_adi},\n\nYarınki randevunuzu hatırlatmak isteriz.\nOda: {oda_adi}\nTarih/Saat: {baslangic_str} – {bitis_str}\n\nGörüşmek üzere."
    return send_mail(to_email, subject, text)


def send_password_reset_email(to_email, reset_url):
    """Şifre sıfırlama bağlantısı — Payafin forgot-password akışı."""
    subject = "Payafin — Şifre sıfırlama"
    text = (
        "Merhaba,\n\n"
        "Şifrenizi sıfırlamak için aşağıdaki bağlantıya tıklayın (1 saat geçerlidir):\n"
        f"{reset_url}\n\n"
        "Bu isteği siz yapmadıysanız bu e-postayı yok sayın."
    )
    return send_mail(to_email, subject, text)


def send_verification_email(to_email, verify_url):
    """Kayıt sonrası e-posta doğrulama bağlantısı."""
    subject = "Payafin — E-postanızı doğrulayın"
    text = (
        "Merhaba,\n\n"
        "Payafin hesabınız oluşturuldu. E-posta adresinizi doğrulamak için "
        "aşağıdaki bağlantıya tıklayın:\n"
        f"{verify_url}\n\n"
        "Hesabınızı kullanmaya hemen başlayabilirsiniz; doğrulama yalnızca "
        "e-posta adresinizin size ait olduğunu teyit eder.\n\n"
        "Bu hesabı siz oluşturmadıysanız bu e-postayı yok sayın."
    )
    return send_mail(to_email, subject, text)


def trigger_randevu_webhook(event, payload):
    """Randevu oluştur/iptal webhook — RANDEVU_WEBHOOK_URL tanımlıysa POST edilir."""
    try:
        url = current_app.config.get("RANDEVU_WEBHOOK_URL")
        if not url:
            return
        import urllib.request
        import urllib.parse
        body = json.dumps({"event": event, "payload": payload})
        req = urllib.request.Request(url, data=body.encode("utf-8"), method="POST", headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass
