# Copyright (c) 2025, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import argparse
import asyncio
import csv
import os
import socket
import struct


validlist = [
    'TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256',
    'TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384',
    'TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256',
    'TLS_AES_128_GCM_SHA256',
    'TLS_AES_256_GCM_SHA384',
    'TLS_CHACHA20_POLY1305_SHA256',
    'TLS_AES_128_CCM_SHA256',
    'TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384',
    'TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305_SHA256',
    'TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305_SHA256',
    'TLS_ECDHE_ECDSA_WITH_AES_256_CCM',
    'TLS_ECDHE_ECDSA_WITH_AES_128_CCM',
    'TLS_EMPTY_RENEGOTIATION_INFO_SCSV'  # this actually isn't a Cipher, but a tool to negotiate which to use
]

def combine_hex_strings(hex_str):
    parts = hex_str.split(',')
    bytes_list = [int(p.strip(), 16) for p in parts]
    combined = b''.join(byte.to_bytes(1, 'big') for byte in bytes_list)
    # maybe working with integers is better? - Not sure, string is simpler to debug
    return '0x' + combined.hex()

def load_cipher_suites(csv_file_path):
    # this uses the CVS file from IANA avaialble from
    # https://www.iana.org/assignments/tls-parameters/tls-parameters.xhtml#tls-parameters-4
    cipher_suites = {}
    with open(csv_file_path, newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            if row['Value'].find("-") != -1 or row["Value"].find("*") != -1:
                # values with dash or asterisk are reserved sections we don't care about
                continue
            hex_code = combine_hex_strings(row['Value'].strip())
            name = row['Description'].strip()
            cipher_suites[hex_code] = name
    return cipher_suites



cipher_suites = load_cipher_suites('tls-parameters-4.csv')

def parse_client_hello(data) -> dict:
    pointer = 0

    content_type = data[pointer]
    pointer += 1

    if content_type != 22:  # 22 = Handshake
        print("Not a handshake message")
        return

    version = struct.unpack('!H', data[pointer:pointer+2])[0]
    pointer += 2

    record_length = struct.unpack('!H', data[pointer:pointer+2])[0]
    pointer += 2

    handshake_type = data[pointer]
    pointer += 1

    if handshake_type != 1:
        print("Not a ClientHello")
        return

    handshake_length = int.from_bytes(data[pointer:pointer+3], 'big')
    pointer += 3

    client_version = struct.unpack('!H', data[pointer:pointer+2])[0]
    pointer += 2

    random_bytes = data[pointer:pointer+32]
    pointer += 32

    session_id_len = data[pointer]
    pointer += 1
    session_id = data[pointer:pointer+session_id_len]
    pointer += session_id_len

    cipher_suites_len = struct.unpack('!H', data[pointer:pointer+2])[0]
    pointer += 2

    cipher_suites = []
    for _ in range(cipher_suites_len // 2):
        suite = struct.unpack('!H', data[pointer:pointer+2])[0]
        cipher_suites.append(f"0x{suite:04x}")
        pointer += 2

    return {
        "version": hex(client_version),
        "random": random_bytes.hex(),
        "session_id": session_id.hex(),
        "cipher_suites": cipher_suites
    }


def start_tls_probe_server_simple_old(host='0.0.0.0', port=4433):
    """This was my first simple implementation
    Reading the TLS Client HEllo and terminating the connection.

    Kept for the momtent in case the proxy ends up causing more trouble"""

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind((host, port))
    server_socket.listen(1)
    print(f"Listening on {host}:{port}")

    while True:
        client_sock, addr = server_socket.accept()
        print(f"\n[+] Connection from {addr}")

        try:
            data = client_sock.recv(4096)
            if data:
                yield parse_client_hello(data)
        except Exception as e:
            print(f"Error reading from client: {e}")
            raise
        finally:
            client_sock.close()


def cprint(count, str, **kwargs):
    print(f"[{count}] {str}", *kwargs)

class ProxyServer:
    def __init__(self, proxy_host='0.0.0.0', proxy_port=443, target_host='kubernetes',
                  target_port=443, close_callback=None, abort_on_error=True):
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        self.target_host = target_host
        self.target_port = target_port
        self.close_callback = close_callback
        self.counter = 0
        self.abort_on_error = abort_on_error

    async def forward_data(self, count, reader, writer):
        while True:
            try:
                data = await reader.read(4096)
                if data:
                    writer.write(data)
                    await writer.drain()
                else:
                    # Connection closed
                    break
            except Exception as e:
                cprint(count, f"Error forwarding data: {e}")
                break

        try:
            writer.close()
        except Exception as e:
            print(f"Error closing writer: {e}")


    async def handle_client(self, client_reader, client_writer):
        self.counter+=1
        count = self.counter

        client_address = client_writer.get_extra_info('peername')
        cprint(count, f"Connection from {client_address}")

        try:
            data = await client_reader.read(4096)
            if not data:
                cprint(count, "Failed to read header from client")
                return

            if self.close_callback:
                if self.close_callback(count, parse_client_hello(data)) and self.abort_on_error:
                    cprint(count, "Aborting connection!")
                    return

            target_reader, target_writer = await asyncio.open_connection(self.target_host, self.target_port)
            cprint(count, f"Connected to target server {self.target_host}:{self.target_port}")

            target_writer.write(data)
            await target_writer.drain()

            client_to_target_task = asyncio.create_task(self.forward_data(count, client_reader, target_writer))
            target_to_client_task = asyncio.create_task(self.forward_data(count, target_reader, client_writer))
            await asyncio.gather(client_to_target_task, target_to_client_task)

        except Exception as e:
            cprint(count, f"Error proxying traffic: {e}")
        finally:
            client_writer.close()

    async def start(self):
        server = await asyncio.start_server(self.handle_client, self.proxy_host, self.proxy_port)
        print(f"Proxy server listening on {self.proxy_host}:{self.proxy_port}")
        return server


def check_client(count, client_hello) -> bool:
    client_ciphers = list(map(lambda hex: cipher_suites.get(hex, hex), client_hello["cipher_suites"]))
    valid = [c for c in client_ciphers if c in validlist]
    invalid = [c for c in client_ciphers if c not in validlist]

    if len(invalid):
        cprint(count, "BAD CIPHERS")
        cprint(count, f"Ciphers offered by client: {client_ciphers}")
        cprint(count, f"Invlaid ciphers: {invalid}")
        return True
    else:
        cprint(count, "ciphers ok")

    return False


async def main():
    host = os.getenv("KUBERNETES_SERVICE_HOST")
    port = os.getenv("KUBERNETES_SERVICE_PORT")

    parser = argparse.ArgumentParser(
                    prog='TLS Validation Proxy',
                    description='Check TLS Ciphers offered by a client')

    parser.add_argument('-A', '--no-abort',
                        help="Don't abort connections requesting bad ciphers",
                        action='store_true')

    args = parser.parse_args()

    abort = not args.no_abort

    print("Creating server")
    proxy_server = ProxyServer(proxy_port=9443, target_host=host, target_port=port, close_callback=check_client, abort_on_error=abort)

    print("Starting server")
    server = await proxy_server.start()
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
     print("calling main")
     asyncio.run(main())
