import re


SUB_STATUS = {
    "SPEDITA": {
        "emoji": "🔴",
        "progress": "█░░░░░░",
        "step": 1,
        "total_steps": 7,
    },
    "GRADING": {
        "emoji": "🟢",
        "progress": "██░░░░░",
        "step": 2,
        "total_steps": 7,
    },
    "ASSEMBLY": {
        "emoji": "🔵",
        "progress": "███░░░░",
        "step": 3,
        "total_steps": 7,
    },
    "QA CHECKS": {
        "emoji": "🟣",
        "progress": "████░░░",
        "step": 4,
        "total_steps": 7,
    },
    "GRADES READY": {
        "emoji": "🟠",
        "progress": "█████░░",
        "step": 5,
        "total_steps": 7,
    },
    "IN RIENTRO": {
        "emoji": "🟡",
        "progress": "██████░",
        "step": 6,
        "total_steps": 7,
    },
    "CHIUSA": {
        "emoji": "✅",
        "progress": "███████",
        "step": 7,
        "total_steps": 7,
    },
}


def normalize_sub_status(status: str | None) -> str:
    """
    Uniforma lo stato letto da Google Sheets.

    Funziona sia con:
    QA CHECKS

    sia con:
    🟣 QA CHECKS
    """
    if not status:
        return ""

    normalized = str(status).strip().upper()

    # Rimuove emoji e simboli iniziali, mantenendo lettere e numeri.
    normalized = re.sub(
        r"^[^A-ZÀ-Ü0-9]+",
        "",
        normalized,
    )

    # Uniforma eventuali spazi multipli.
    normalized = " ".join(normalized.split())

    return normalized


def get_sub_status_info(status: str | None) -> dict:
    """
    Restituisce emoji, barra e numero della fase.
    """
    normalized_status = normalize_sub_status(status)

    default_info = {
        "name": normalized_status or "STATO NON DISPONIBILE",
        "emoji": "⚪",
        "progress": "░░░░░░░",
        "step": 0,
        "total_steps": 7,
    }

    status_info = SUB_STATUS.get(normalized_status)

    if status_info is None:
        return default_info

    return {
        "name": normalized_status,
        **status_info,
    }