"""Keep bearer/OAuth credentials on their original HTTP origin during redirects.

The application uses stdlib urlopen throughout. Install its redirect policy once,
before workers start, preserving the shared network test hook and proxy behavior.
"""

import urllib.error
import urllib.parse
import urllib.request


def _origin(url):
    parsed = urllib.parse.urlsplit(url)
    port = parsed.port
    if port is None:
        port = {"https": 443, "http": 80}.get(parsed.scheme)
    return parsed.scheme, parsed.hostname, port


class AuthorizationRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            try:
                same_origin = _origin(req.full_url) == _origin(redirected.full_url)
            except ValueError:
                raise urllib.error.HTTPError(req.full_url, code, "Invalid redirect origin", headers, fp) from None
            if not same_origin:
                redirected.remove_header("Authorization")
        return redirected


urllib.request.install_opener(urllib.request.build_opener(AuthorizationRedirectHandler()))
