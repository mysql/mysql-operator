# Copyright (c) 2025, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import argparse
import json
import logging
import ssl
import sys

from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import unquote as urlunquote

from . import meb_controller as meb

class CustomHandler(SimpleHTTPRequestHandler):
    datadir = None

    def do_GET(self):

        if self.path == "/ping":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"true\n")
            return

        if self.path == "/restart":
            # Debug hook to quickly restart container
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"true\n")
            sys.exit()

    def do_POST(self):
        # We don't do do access cheking here as Python http.server will
        # verify the client certificate before reaching this point

        if self.path.startswith('/backup/'):
            try:
                backup_name = urlunquote(self.path.rsplit('/', 1)[-1])

                content_length = int(self.headers.get('Content-Length', 0))
                post_body = self.rfile.read(content_length)

                info = json.loads(post_body)
                backup_profile = info["spec"]

                username = info["source"]["user"]
                password = info["source"]["password"]

                options = ["-u", username, f"-p{password}", f"--host=127.0.0.1"]
                if 'extra_options' in backup_profile and backup_profile['extra_options']:
                    options += backup_profile['extra_options']


                storage_opts = backup_profile['storage']

                if "s3" in storage_opts:
                    storage = meb.MebStorageS3(
                        storage_opts["s3"]["objectKeyPrefix"] + backup_name,
                        storage_opts["s3"]["region"], storage_opts["s3"]["bucket"],
                        info["secret"]["accessKeyId"], info["secret"]["secretAccessKey"],
                        storage_opts["s3"]["host"] if "host" in storage_opts["s3"] else None)

                elif "oci" in storage_opts:
                    storage = meb.MebStorageOCIPAR(storage_opts['oci']['prefix']+backup_name,
                                                meb.OCIObjectStorage(
                                                    meb.OCIRequest(info['secret']),
                                                    storage_opts['oci']['bucketName'],
                                                    storage_opts['oci']['namespace']))
                else:
                    raise Exception("Need either meb or s3 storage specification")

                incremental = info["incremental"]
                incremental_base = info["incremental_base"]

                backup = meb.MySQLEnterpriseBackup(
                    storage,
                    options,
                    "/tmp/backup-tmp",
                )
                try:
                    backup.backup(incremental, 'history:'+incremental_base)
                except Exception as exc:
                    self.send_response(500)
                    self.end_headers()
                    response = backup.log + "\n" + exc.__str__()
                    self.wfile.write(response.encode('utf-8', errors='replace'))
                    return

                finally:
                    backup.cleanup()

                self.send_response(200)
                self.end_headers()
                self.wfile.write(backup.log.encode('utf-8', errors='replace'))
                return
            except Exception as exc:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"Failure\n")
                raise


        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"false\n")


def serve_http(sslopts: dict, datadir: str):
    server_address = ('0.0.0.0', 4443)

    handler_class = CustomHandler
    handler_class.datadir = datadir

    httpd = HTTPServer(server_address, handler_class)

    httpd.socket = ssl.wrap_socket(httpd.socket,
                                   server_side=True,
                                   certfile=sslopts["cert"],
                                   keyfile=sslopts["key"],
                                   cert_reqs=ssl.CERT_REQUIRED,
                                   ca_certs='/tls/ca.pem',
                                   ssl_version=ssl.PROTOCOL_TLS)

    httpd.serve_forever()

def main(argv):
    parser = argparse.ArgumentParser(description="MySQL InnoDB Cluster MySQL Enterprise Backup Daemon")
    parser.add_argument('--logging-level', type=int, nargs="?", default = logging.INFO, help="Logging Level")
    parser.add_argument('--pod-name', type=str, nargs=1, default=None, help="Pod Name")
    parser.add_argument('--pod-namespace', type=str, nargs=1, default=None, help="Pod Namespace")
    parser.add_argument('--datadir', type=str, default="/var/lib/mysql", help="Path do data directory")
    parser.add_argument('--ssl-cert', type=str, help="Path do TLS server cert")
    parser.add_argument('--ssl-key', type=str, help="Path do TLS server key")
    args = parser.parse_args(argv[1:])

    datadir = args.datadir

    logging.basicConfig(level=args.logging_level,
                        format='%(asctime)s - [%(levelname)s] [%(name)s] %(message)s',
                        datefmt="%Y-%m-%dT%H:%M:%S")
    logger = logging.getLogger("meb")

    name = args.pod_name[0]
    namespace = args.pod_namespace[0]

    sslopts = {
        "cert": args.ssl_cert,
        "key": args.ssl_key,
    }

    try:
        logger.info("Starting MEB Daemon for %s/%s", namespace, name)

        serve_http(sslopts, datadir)
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.critical(f"Unhandled exception while restoring: {e}")
        return 1

if __name__ == '__main__':
    main(sys.argv)
