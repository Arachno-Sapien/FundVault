#!/usr/bin/env python
import os
import sys
from pathlib import Path


def main():
    # Auto-load backend/.env so os.getenv() picks up GEMINI_API_KEY etc.
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fundvault_backend.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Install dependencies from backend/requirements.txt."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
