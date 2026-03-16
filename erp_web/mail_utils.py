# -*- coding: utf-8 -*-
"""Basit e-posta gönderimi — randevu onay, iptal, hatırlatma; webhook tetikleme."""
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import current_app


def send_mail(to_email, subject, body_text, body_html=None):
    """Tek alıcıya e-posta gönder. MAIL_* config gerekli."""
    if not to_email or not (current_app.config.get("MAIL_USERNAME") and current_app.config.get("MAIL_PASSWORD")):
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = current_app.config.get("MAIL_DEFAULT_SENDER", "noreply@example.com")
        msg["To"] = to_email
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
        if body_html:
            msg.attach(MIMEText(body_html, "html", "utf-8"))
        with smtplib.SMTP(current_app.config.get("MAIL_SERVER", "smtp.gmail.com"), current_app.config.get("MAIL_PORT", 587)) as s:
            if current_app.config.get("MAIL_USE_TLS"):
                s.starttls()
            s.login(current_app.config["MAIL_USERNAME"], current_app.config["MAIL_PASSWORD"])
            s.sendmail(msg["From"], to_email, msg.as_string())
        return True
    except Exception as e:
        if current_app.debug:
            print("send_mail error:", e)
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
