import unittest
import threading
from urllib import request, error, parse
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import calendar_oauth
from rich_menu import definition, BUTTONS


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class MenuOAuthTests(unittest.TestCase):
    def test_six_areas_cover_menu_without_overlap(self):
        data = definition()
        area = 0
        rects = []
        for item in data['areas']:
            b = item['bounds']
            rect = b['x'], b['y'], b['x']+b['width'], b['y']+b['height']
            self.assertTrue(0 <= rect[0] < rect[2] <= 2500)
            self.assertTrue(0 <= rect[1] < rect[3] <= 1686)
            for other in rects:
                self.assertFalse(rect[0] < other[2] and rect[2] > other[0] and rect[1] < other[3] and rect[3] > other[1])
            rects.append(rect)
            area += b['width']*b['height']
        self.assertEqual(area, 2500*1686)
        self.assertEqual([x['action']['text'] for x in data['areas']], [x[2] for x in BUTTONS])

    def test_oauth_requires_csrf_and_rejects_foreign_origin(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), calendar_oauth.OAuthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        opener = request.build_opener(NoRedirect)
        def post(csrf, origin):
            data = parse.urlencode({'csrf':csrf}).encode()
            req = request.Request(f'http://127.0.0.1:{server.server_port}/connect', data,
                {'Host':'127.0.0.1:8791', 'Origin':origin, 'Content-Type':'application/x-www-form-urlencoded'})
            try:
                response = opener.open(req)
            except error.HTTPError as exc:
                response = exc
            from types import SimpleNamespace
            output = SimpleNamespace(code=response.code,headers=response.headers)
            response.close()
            return output
        try:
            self.assertEqual(post('invalid', 'null').code, 403)
            self.assertEqual(post(calendar_oauth.connect_csrf, 'https://other.example').code, 403)
            with patch('calendar_oauth.read_client', return_value={'client_id':'test.apps.googleusercontent.com'}):
                response = post(calendar_oauth.connect_csrf, 'null')
            self.assertEqual(response.code, 303)
            target = parse.urlsplit(response.headers['Location'])
            args = parse.parse_qs(target.query)
            self.assertEqual(target.netloc, 'accounts.google.com')
            self.assertEqual(args['code_challenge_method'], ['S256'])
            self.assertIn(args['state'][0], calendar_oauth.sessions)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
