class AuthRequiredMiddleware:
    """Reject unauthenticated requests to /api/* except /api/login."""

    PUBLIC = {"/api/login"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.rstrip("/") not in self.PUBLIC and request.path.startswith("/api/"):
            if not request.session.get("authenticated"):
                from django.http import JsonResponse
                return JsonResponse({"error": "Unauthorized"}, status=401)
        return self.get_response(request)
