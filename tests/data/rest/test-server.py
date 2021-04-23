#!/usr/bin/env python
#
# Copyright (c) 2019, Oracle and/or its affiliates. All rights reserved.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is also distributed with certain software (including
# but not limited to OpenSSL) that is licensed under separate terms, as
# designated in a particular file or component or in included license
# documentation.  The authors of MySQL hereby grant you an additional
# permission to link the program and your derivative works with the
# separately licensed software that they have included with MySQL.
# This program is distributed in the hope that it will be useful,  but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA
#

from __future__ import print_function

try:
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from socketserver import ThreadingMixIn
except:
    from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
    from SocketServer import ThreadingMixIn

import base64
import json
import os
import re
import ssl
import sys
import time
import traceback

try:
    from urllib.parse import parse_qsl, urlparse
except:
    from urlparse import parse_qsl, urlparse


class TestRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, **kwargs):
        self._handlers = {
            r'^/timeout/([0-9]*\.?[0-9]*)$': self.handle_timeout,
            r'^/redirect/([1-9][0-9]*)$': self.handle_redirect,
            r'^/server_error/([1-9][0-9]*)$': self.handle_server_error,
            r'^/basic/([^/]+)/(.+)$': self.handle_basic,
            r'^/headers?.+$': self.handle_headers
        }

        try:
            BaseHTTPRequestHandler.__init__(self, *args, **kwargs)
        except Exception as e:
            self.log_message(traceback.format_exc())

    def do_GET(self):
        self.handle_request()

    def do_HEAD(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def do_PUT(self):
        self.handle_request()

    def do_PATCH(self):
        self.handle_request()

    def do_DELETE(self):
        self.handle_request()

    def handle_timeout(self, args):
        seconds = min(10, float(args[0]))
        time.sleep(seconds)
        self.reply()
        return True

    def handle_redirect(self, args):
        n = min(50, int(args[0]))
        self.send_response(302)
        self.send_header(
            'Location', '/%s' % self.command.lower()
            if n == 1 else '/redirect/%d' % (n - 1))
        self.send_header('Content-Length', '0')
        self.end_headers()
        return True

    def handle_basic(self, args):
        user = args[0]
        password = args[1]
        authorization = self.getheader('Authorization', 'Basic ')
        authenticated = ('%s:%s' % (user, password)) == base64.b64decode(
            authorization[6:]).decode('ascii')
        self.reply(200 if authenticated else 401,
                   {'authentication': 'OK' if authenticated else 'NO'})
        return True

    def handle_server_error(self, args):
        self.reply(status=int(args[0]))
        return True

    def handle_headers(self, args):
        self.reply(extra_headers=parse_qsl(urlparse(self.path).query, keep_blank_values=True))
        return True

    def invoke_handler(self):
        for path, handler in self._handlers.items():
            m = re.match(path, self.path)
            if m:
                return handler(m.groups())

        return False

    def handle_request(self):
        if not self.invoke_handler():
            self.reply()

    def reply(self, status=200, extra_response={}, extra_headers={}):
        response = {}
        response['method'] = self.command
        response['path'] = self.path
        response['headers'] = self.getheaders()
        response['data'], response['json'] = self.get_request_body()
        response.update(extra_response)
        response = json.dumps(response)

        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', '%d' % (len(response)))

        for h in extra_headers:
            self.send_header(h[0], h[1])

        self.end_headers()

        if self.command != 'HEAD':
            self.wfile.write(response.encode('ascii'))

    def get_request_body(self):
        content_length = int(self.getheader('Content-Length', 0))
        content = None
        json_content = None

        if content_length > 0:
            content = self.rfile.read(content_length).decode('ascii')

            if self.getheader('Content-Type', 'unknown') == 'application/json':
                json_content = json.loads(content)

        return content, json_content

    def log_message(self, format, *args):
        BaseHTTPRequestHandler.log_message(self, format, *args)
        sys.stderr.flush()

    def getheader(self, name, default):
        if hasattr(self.headers, 'getheader'):
            return self.headers.getheader(name, default)
        else:
            return self.headers.get(name, default)

    def getheaders(self):
        headers = {}
        for k, v in dict(self.headers).items():
            headers[k.lower()] = v
        return headers

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

def usage():
    print('Usage:')
    print('')
    print(' ', os.path.basename(__file__), 'port')
    print('')


def test_server(port):
    server = ThreadedHTTPServer(('127.0.0.1', port), TestRequestHandler)
    ssl_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'ssl')
    server.socket = ssl.wrap_socket(
        server.socket,
        keyfile=os.path.join(ssl_dir, 'key.pem'),
        certfile=os.path.join(ssl_dir, 'cert.pem'),
        server_side=True)

    print('HTTPS test server running on 127.0.0.1:%d' % port)
    sys.stdout.flush()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

    server.server_close()
    print('HTTPS test server stopped')
    sys.stdout.flush()


def main(args):
    if len(args) != 1:
        usage()
    else:
        test_server(int(args[0]))


if __name__ == '__main__':
    main(sys.argv[1:])
