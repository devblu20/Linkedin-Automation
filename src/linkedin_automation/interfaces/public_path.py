"""Serve the same authenticated dashboard below its isolated public URL path."""

from starlette.responses import RedirectResponse

PUBLIC_PATH = "/linkedin-automation"


class PublicPathMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] == "http" and path == PUBLIC_PATH:
            # Keep the browser's Basic-auth protection space inside this directory.
            query = scope.get("query_string", b"").decode("latin-1")
            target = PUBLIC_PATH + "/" + ("?" + query if query else "")
            response = RedirectResponse(target, headers={"Cache-Control": "no-store"})
            await response(scope, receive, send)
            return
        if scope["type"] == "http" and path.startswith(PUBLIC_PATH + "/"):
            scope = dict(scope)
            scope["path"] = path[len(PUBLIC_PATH) :] or "/"
            scope["raw_path"] = scope["path"].encode("utf-8")
            scope["state"] = {**scope.get("state", {}), "dashboard_prefix": PUBLIC_PATH}
        await self.app(scope, receive, send)
