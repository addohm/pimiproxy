from urllib.parse import urlparse

from django.http import JsonResponse


class AuthRequiredMiddleware:
    """Reject unauthenticated requests to /api/* except /api/login."""

    PUBLIC = {"/api/login"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.rstrip("/") not in self.PUBLIC and request.path.startswith("/api/"):
            if not request.session.get("authenticated"):
                return JsonResponse({"error": "Unauthorized"}, status=401)
        return self.get_response(request)


class OriginCheckMiddleware:
    """CSRF defense for /api/* mutating requests.

    Every POST/PUT/PATCH/DELETE hitting /api/* must carry an Origin (or
    Referer, for clients that omit Origin) whose host matches the request's
    own Host header. Same-origin browser requests naturally satisfy this;
    cross-origin attacks that ride a stolen session cookie do not.

    Curl/CLI tools often omit both — we only fail when one is present and
    mismatches. A request with neither header is allowed through so local
    scripts (e.g. the installer's setup hooks) still work.
    """

    SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method not in self.SAFE_METHODS and request.path.startswith("/api/"):
            origin_header = request.META.get("HTTP_ORIGIN") or request.META.get("HTTP_REFERER")
            if origin_header:
                origin_host = urlparse(origin_header).netloc
                request_host = request.get_host()
                if origin_host and origin_host != request_host:
                    return JsonResponse(
                        {"error": f"Origin mismatch: {origin_host} != {request_host}"},
                        status=403,
                    )
        return self.get_response(request)
