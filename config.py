from datetime import timedelta


class Config:
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Billing
    GST_RATE = 0.05

    # Kitchen refresh
    KITCHEN_REFRESH_MS = 15000

    # Security-ish
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(days=1)

