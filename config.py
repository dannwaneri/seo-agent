import os


def get_smtp_config() -> dict | None:
    keys = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM")
    values = {k: os.environ.get(k) for k in keys}
    if any(v is None or v == "" for v in values.values()):
        return None
    return {
        "host":     values["SMTP_HOST"],
        "port":     int(values["SMTP_PORT"]),
        "user":     values["SMTP_USER"],
        "password": values["SMTP_PASSWORD"],
        "from":     values["SMTP_FROM"],
    }


def get_pagespeed_key() -> str | None:
    return os.environ.get("PAGESPEED_API_KEY") or None
