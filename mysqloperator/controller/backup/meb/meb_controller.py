# Copyright (c) 2025, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/

import base64
import datetime
import json
import shutil
import subprocess
import sys
import urllib.parse

import http.client
from urllib.parse import urlparse

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.hashes import SHA256

import threading
from datetime import timedelta

MYSQLBACKUP_BINARY = '/usr/bin/mysqlbackup'

MYCNF_PATH = '/etc/my.cnf'




class MebStorageLocalImage:
    def __init__(self, target_path):
        self.target_path = target_path

    def get_backup_args(self):
        return ['--backup-image='+self.target_path]


class OCIRequest:
    def __init__(self, credentials):
        self.tenancy_id = credentials['tenancy']
        self.user_id = credentials['user']
        self.fingerprint = credentials['fingerprint']
        self.private_key = credentials['privatekey']
        self.region = credentials['region']

    def request(self, host: str, request_target: str, method: str,
                request_body: dict) -> dict:
        private_key = serialization.load_pem_private_key(
            self.private_key.encode('utf-8'),
            password=None
        )

        endpoint = f'https://{host}{request_target}'

        date_header = datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')
        headers = {
            "Content-Type": "application/json",
            "date": date_header,
            "host": host,
            "opc-client-info": "public-objectstorage:0.1.0"
        }

        signing_string = f"(request-target): {method.lower()} {request_target}\n" \
                         f"host: {host}\n" \
                         f"date: {headers['date']}\n" \
                         f"opc-client-info: {headers['opc-client-info']}"

        # Sign the string
        signature = base64.b64encode(
            private_key.sign(
                signing_string.encode('utf-8'),
                padding.PKCS1v15(),
                SHA256()
            )
        ).decode('utf-8')

        authorization_header = (
            f'Signature version="1",'
            f'headers="(request-target) host date opc-client-info",'
            f'keyId="{self.tenancy_id}/{self.user_id}/{self.fingerprint}",'
            f'algorithm="rsa-sha256",'
            f'signature="{signature}"'
        )
        headers['authorization'] = authorization_header

        data = json.dumps(request_body) if request_body else None

        parsed_url = urlparse(endpoint)
        conn = http.client.HTTPSConnection(parsed_url.netloc)

        path = parsed_url.path
        if parsed_url.query:
            path += "?" + parsed_url.query

        headers = headers or {}
        if data:
            headers['Content-Type'] = 'application/json'

        conn.request(method, path, body=data, headers=headers)
        response = conn.getresponse()

        if response.status not in (200, 204):
            print(f"Failed to do OCI request: {response.status}")
            print(response.read().decode())
            raise Exception("Failed OCI API call")

        response_data = response.read()
        if response_data:
            return json.loads(response_data)

        return True


class OCIObjectStorage:
    def __init__(self, oci: OCIRequest, bucket_name: str, namespace_name: str):
        self.oci = oci
        self.bucket_name = bucket_name
        self.namespace_name = namespace_name

    def create_par(self, name:str, object_name: str, access_type: str, expiry: timedelta) -> str:
        host = f'objectstorage.{self.oci.region}.oraclecloud.com'
        request_target = f"/n/{self.namespace_name}/b/{self.bucket_name}/p"

        expiration_time = (datetime.datetime.utcnow() + expiry).strftime('%Y-%m-%dT%H:%M:%SZ')
        request_body = {
            "name": name,
            "objectName": object_name,
            "accessType": access_type,
            "timeExpires": expiration_time
        }

        response = self.oci.request(host, request_target, "POST", request_body)
        return (f'https://{host}{response["accessUri"]}', response['id'])


    def delete_par(self, id: str) -> None:
        id = urllib.parse.quote(id)
        host = f'objectstorage.{self.oci.region}.oraclecloud.com'
        request_target = f"/n/{self.namespace_name}/b/{self.bucket_name}/p/{id}"

        self.oci.request(host, request_target, "DELETE", None)

class MebStorageOCIPAR:
    def __init__(self, target_path, ocios: OCIObjectStorage):
        self.target_path = target_path
        self.ocios = ocios
        self.pars_to_delete = []
        self.hidden = []


    def clean(self):
        for par_id in self.pars_to_delete:
            try:
                self.ocios.delete_par(par_id)
            except Exception as exc:
                # Don't fail during cleanup
                print(f"Failed to delete PAR {par_id}: {exc}")


    def get_args(self):
        (par, par_id) = self.ocios.create_par("backup-par",
                                              self.target_path,
                                              "ObjectWrite",
                                              timedelta(hours=1))

        par_url=par.replace(self.target_path, "")
        self.pars_to_delete.append(par_id)
        self.hidden.append(par_url)

        return [
            '--backup-image=-',
            '--cloud-service=OCI',
            '--cloud-object='+self.target_path,
            '--cloud-par-url='+par.replace(self.target_path, "")
        ]

    def get_filter(self):
        return self.hidden


class MebStorageOCIRestore:
    par_base_url: str = ""

    def __init__(self, par_base_url: str):
        self.par_base_url = par_base_url

    def get_args(self, source):
        return [
            '--backup-image=-',
            '--cloud-service=OCI',
            '--cloud-par-url='+self.par_base_url+source,
        ]

    def get_filter(self):
        return [self.par_base_url]


class MebStorageS3:
    def __init__(self, object_key:str, region:str, bucket:str,
                 key_id:str, secret_access_key:str,
                 host:str=None):

        self.opts = [
            "--cloud-service=s3",
            "--cloud-aws-region="+region,
            "--cloud-access-key-id="+key_id,
            "--cloud-secret-access-key="+secret_access_key,
            "--cloud-bucket="+bucket,
            "--cloud-object-key="+object_key,
            "--backup-image=-"
        ]

        if host:
            self.opts.append("--cloud-host="+host)


    def get_args(self):
        return self.opts

    def get_filter(self):
        return None


class MySQLEnterpriseBackup:
    def __init__(self, storage, auth_options: list, tmppath: str):
        self.auth_options = auth_options
        self.storage = storage
        self.tmppath = tmppath
        self.log = ""

    def _run_cmd(self, cmd: str, args=None):
        command = [
            MYSQLBACKUP_BINARY,
            '--backup-dir='+self.tmppath
        ]

        if args:
            command += args

        if cmd != "copy-back-and-apply-log":
            command += self.storage.get_args() + self.auth_options

        command.append(cmd)

        def print_output(pipe, stream):
            hidden = self.storage.get_filter()

            try:
                for line in iter(pipe.readline, ''):
                    if line:
                        if hidden:
                            # Avoid printing PAR URLs and similar into logs
                            for term in hidden:
                                line = line.replace(term, 'X' * len(term))

                        self.log += line

                        stream.write(line)
                        stream.flush()
            finally:
                pipe.close()

        # in order to pass password via stdin we got to also pipe stdout/stderr
        # through our code, if we end up using localroot (passwordless) we
        # might use the simpler subprocess.run ...
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
            text=True
        )

        process.stdin.close()

        stdout_thread = threading.Thread(target=print_output, args=(process.stdout, sys.stdout))
        stderr_thread = threading.Thread(target=print_output, args=(process.stderr, sys.stderr))

        stdout_thread.start()
        stderr_thread.start()

        stdout_thread.join()
        stderr_thread.join()

        error_code = process.wait()

        if error_code:
            raise RuntimeError(f"Running Backup failed: {error_code}")

        return True


    def backup(self, incremental=False, incremental_base='history:last_backup'):
        if incremental:
            args = [
                '--incremental', '--incremental-base='+incremental_base,
            ]
            return self._run_cmd('backup-to-image', args)

        return self._run_cmd('backup-to-image')

    def extract(self, image: str):
        command = [
            MYSQLBACKUP_BINARY,
            '--backup-dir='+self.tmppath,
            '--datadir=/var/lib/mysql'
        ]

        subprocess.run(command + self.storage.get_args(image) + ['image-to-backup-dir'], check=True)

    def restore(self, full_image: str, incrementals: list = ()):
        command = [
            #'--defaults-file='+MYCNF_PATH,
            '--backup-dir='+self.tmppath,
            '--datadir=/var/lib/mysql'
        ]

        try:
            self._run_cmd('copy-back-and-apply-log', args=command+self.storage.get_args(full_image))

        except FileNotFoundError as exc:
            if exc.filename == MYSQLBACKUP_BINARY:
                raise NotImplementedError("The MySQL Operator image does " \
                                          "not contain MySQL Enterprise " \
                                          "Backup, please verify you are " \
                                          "using MySQL Enterprise Edition"
                                          ) from exc
            raise
        except  RuntimeError: # subprocess.CalledProcessError:
            sys.exit(1)


        # /var/run/mysqld is a location shared between restore initContainer,
        # which creates this file and sidecar container, which wants to read it
        shutil.copy(self.tmppath+'/meta/backup_variables.txt', '/var/run/mysqld/backup_variables.txt')
        shutil.rmtree(self.tmppath)

        for incremental in incrementals:
            self._run_cmd('copy-back-and-apply-log', args=command+['--incremental']+self.storage.get_args(incremental))
            # This will override hte previous - we are only interested to keep the last
            shutil.copy(self.tmppath+'/meta/backup_variables.txt', '/var/run/mysqld/backup_variables.txt')
            shutil.rmtree(self.tmppath)


    def cleanup(self):
        if hasattr(self.storage, 'clean'):
            self.storage.clean()

        try:
            shutil.rmtree(self.tmppath)
        except Exception as exc:
            print(f"Failed to delete temp directory, this is likely okay: {exc}")

def testmain():
    import argparse

    parser = argparse.ArgumentParser(
                    prog='Backup Test Interrface',
                    description='Create MySQL Enterprise Backup')

    parser.add_argument("target")
    parser.add_argument("-u", "--user")
    parser.add_argument("-p", "--password")
    parser.add_argument("-t", "--host") # argparse wants -h for itself ...
    parser.add_argument("-i", "--incremental", action='store_true')

    args = parser.parse_args()

    storage = MebStorageOCIPAR(args.target, OCIObjectStorage(OCIRequest(), 'jschluet', 'idylsdbcgx0d'))

    meb = MySQLEnterpriseBackup(
        storage,
        [f"-u{args.user}", f"-p{args.password}", f"--host={args.host}"],
        "/tmp/backup-tmp"
    )
    try:
        meb.backup(args.incremental)
    finally:
        meb.cleanup()


if __name__ == "__main__":
    testmain()
