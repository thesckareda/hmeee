import logging
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv
from twilio.rest import Client

logger = logging.getLogger(__name__)

# Twilio WhatsApp outbound media limit is 16 MB.
MAX_WHATSAPP_MEDIA_BYTES = 16 * 1024 * 1024

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
FROM_WHATSAPP = os.environ.get("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886").strip()

BROCHURES = {
    "godrej": {
        "label": "Godrej",
        "url": "https://regal-pavilion.com/godrej/assets/pdf/project_brochure.pdf?v=1764831828",
        "maps_url": "https://maps.app.goo.gl/9cdRQiAsgBq51GWy9",
    },
    "myhome": {
        "label": "My Home Udyan",
        "url": "https://www.myhomeconstructions.com/my-home-udyan/assets/brochure.pdf",
        "maps_url": "https://maps.app.goo.gl/xnEkP9S7RdCvZFxy5",
    },
    "signature": {
        "label": "Signature",
        "url": "https://www.signaturedevelopers.in/download/e-brochure.pdf",
        "maps_url": "https://maps.app.goo.gl/PvpXZxBMfR1jya18A",
    },
    "housing": {
        "label": "Housing",
        "url": "https://housing-is-01.s3.amazonaws.com/6a32315a/f76c4291e4698e875e24eff9caecd1c3/original.pdf",
    },
    "indroyal": {
        "label": "Ind Royal",
        "url": "https://indroyalproperties.com/wp-content/uploads/2015/12/brochure.pdf",
    },
    "tricolt": {
        "label": "Tricolt",
        "url": "https://www.tricolt.co.za/brochure.pdf",
    },
    "trilight": {
        "label": "The Trilight",
        "url": "https://thetrilight.com/wp-content/uploads/2023/02/Trilight_Digital_Brochure.pdf",
        "maps_url": "https://maps.app.goo.gl/fKEXUVDrxSFgRsQF6",
    },
}

# Properties with a site-location link but no brochure PDF.
SITE_ONLY_PROPERTIES = {
    "trident": {
        "label": "Trident",
        "maps_url": "https://maps.app.goo.gl/anpRBGMyzJMSSahd9",
    },
}

BROCHURE_ALIASES = {
    "my-home": "myhome",
    "my_home": "myhome",
    "my home": "myhome",
    "my home udyan": "myhome",
    "udyan": "myhome",
    "triednt": "trident",
    "trident": "trident",
    "the trident": "trident",
    "ind-royal": "indroyal",
    "ind_royal": "indroyal",
    "ind royal": "indroyal",
    "indroyal properties": "indroyal",
    "tri-colt": "tricolt",
    "tri colt": "tricolt",
    "tri-light": "trilight",
    "tri light": "trilight",
    "the trilight": "trilight",
}

_client: Client | None = None


def _get_client() -> Client:
    global _client
    if _client is None:
        if not ACCOUNT_SID or not AUTH_TOKEN:
            raise RuntimeError(
                "Set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN in .env (see .env.example)"
            )
        _client = Client(ACCOUNT_SID, AUTH_TOKEN)
    return _client


def _to_whatsapp(number: str) -> str:
    cleaned = number.strip()
    if cleaned.startswith("whatsapp:"):
        return cleaned
    if not cleaned.startswith("+"):
        cleaned = f"+{cleaned.lstrip('+')}"
    return f"whatsapp:{cleaned}"


def _normalize_property_key(property_key: str) -> str:
    return BROCHURE_ALIASES.get(property_key.strip().lower(), property_key.strip().lower())


def resolve_brochure(brochure_key: str) -> tuple[str, str]:
    key = _normalize_property_key(brochure_key)
    brochure = BROCHURES.get(key)
    if not brochure:
        available = ", ".join(sorted(BROCHURES))
        raise ValueError(f"Unknown brochure '{brochure_key}'. Available: {available}")
    return brochure["label"], brochure["url"]


def resolve_property(property_key: str) -> dict | None:
    """Resolve a property name/ID to label, maps link, and optional brochure."""
    if not property_key or not str(property_key).strip():
        return None
    key = _normalize_property_key(str(property_key))
    brochure = BROCHURES.get(key)
    if brochure:
        return {
            "key": key,
            "label": brochure["label"],
            "maps_url": brochure.get("maps_url", ""),
            "brochure_url": brochure.get("url", ""),
            "has_brochure": bool(brochure.get("url")),
        }
    site_only = SITE_ONLY_PROPERTIES.get(key)
    if site_only:
        return {
            "key": key,
            "label": site_only["label"],
            "maps_url": site_only.get("maps_url", ""),
            "brochure_url": "",
            "has_brochure": False,
        }
    return None


def property_has_brochure(property_key: str) -> bool:
    resolved = resolve_property(property_key)
    return bool(resolved and resolved.get("has_brochure"))


def list_brochures() -> list[str]:
    return sorted(BROCHURES)


def get_media_size_bytes(media_url: str) -> int | None:
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            response = client.head(media_url)
            if response.status_code >= 400:
                response = client.get(media_url, headers={"Range": "bytes=0-0"})
            size = response.headers.get("content-length")
            return int(size) if size and size.isdigit() else None
    except Exception as exc:
        logger.warning("Could not check brochure size for %s: %s", media_url, exc)
        return None


def send_brochure_via_whatsapp(to_number_e164: str, brochure_key: str) -> dict[str, str]:
    """
    to_number_e164: recipient number in E.164, e.g. '+9198XXXXXXX'
    brochure_key: godrej | myhome | signature | housing | indroyal | tricolt | trilight
    """
    label, media_url = resolve_brochure(brochure_key)
    to_whatsapp = _to_whatsapp(to_number_e164)

    client = _get_client()
    media_size = get_media_size_bytes(media_url)
    too_large = media_size is not None and media_size > MAX_WHATSAPP_MEDIA_BYTES

    if too_large:
        size_mb = media_size / (1024 * 1024)
        msg_text = client.messages.create(
            from_=FROM_WHATSAPP,
            to=to_whatsapp,
            body=(
                f"Here is the {label} brochure ({size_mb:.1f} MB — too large for WhatsApp attachment). "
                f"Download it here:\n{media_url}"
            ),
        )
        return {
            "brochure": brochure_key,
            "label": label,
            "delivery": "link",
            "media_size_bytes": str(media_size),
            "text_sid": msg_text.sid,
        }

    msg_text = client.messages.create(
        from_=FROM_WHATSAPP,
        to=to_whatsapp,
        body=f"I'm sending the {label} brochure now. Please check your WhatsApp.",
    )
    msg_media = client.messages.create(
        from_=FROM_WHATSAPP,
        to=to_whatsapp,
        body=f"{label} Brochure",
        media_url=[media_url],
    )
    return {
        "brochure": brochure_key,
        "label": label,
        "delivery": "media",
        "text_sid": msg_text.sid,
        "media_sid": msg_media.sid,
    }


def _print_usage() -> None:
    brochures = ", ".join(list_brochures())
    print(f"Usage: python3 send_whatsapp_media.py <brochure> <phone>")
    print(f"       python3 send_whatsapp_media.py --list")
    print(f"Brochures: {brochures}")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] in ("--list", "-l"):
        for key in list_brochures():
            print(f"{key}: {BROCHURES[key]['label']}")
        sys.exit(0)

    if len(sys.argv) != 3:
        _print_usage()
        sys.exit(1)

    try:
        print(send_brochure_via_whatsapp(sys.argv[2], sys.argv[1]))
    except ValueError as exc:
        print(exc)
        _print_usage()
        sys.exit(1)
    except RuntimeError as exc:
        print(exc)
        sys.exit(1)