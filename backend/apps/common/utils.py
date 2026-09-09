import json
import random
import string

from django.http import JsonResponse
from django.utils import timezone


def uid():
    prefix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"{prefix}{int(timezone.now().timestamp() * 1000):x}"


def parse_body(request):
    if not request.body:
        return {}
    try:
        return json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


def json_error(message, status=400):
    return JsonResponse({"error": message}, status=status)
