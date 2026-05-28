#!/bin/bash
#
# Copyright (c) 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/

set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT_ROOT="$SCRIPT_DIR/out/meb"
DOMAIN="cluster.local"
VALIDITY_DAYS=3650
CA_NAME="Test_CA"
MEB_RDN_CN="backupclient"
KEY_BITS=3072

function make_key() {
  key=$1

  openssl genpkey -algorithm RSA \
          -pkeyopt rsa_keygen_bits:$KEY_BITS \
          -pkeyopt rsa_keygen_pubexp:65537 \
          -out "$key"
}

function write_ca_req() {
  out_dir=$1

  cat > "$out_dir/ca-req.conf" << EOF
[req]
distinguished_name = req_distinguished_name
x509_extensions = v3_ca
prompt = no
[req_distinguished_name]
CN = $CA_NAME
[v3_ca]
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always,issuer
basicConstraints = critical, CA:true
keyUsage = critical, keyCertSign, cRLSign
EOF
}

function write_server_req() {
  out_dir=$1
  ns=$2
  cluster=$3

  cat > "$out_dir/server-req.conf" << EOF
[req]
distinguished_name = req_distinguished_name
x509_extensions = v3_req
prompt = no
[req_distinguished_name]
O=$cluster
[v3_req]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = serverAuth, clientAuth
subjectAltName = @alt_names
[alt_names]
DNS.1 = *.$cluster-instances.$ns.svc.$DOMAIN
DNS.2 = *.$cluster-instances
EOF
}

function write_router_req() {
  out_dir=$1
  ns=$2
  cluster=$3

  cat > "$out_dir/router-req.conf" << EOF
[req]
distinguished_name = req_distinguished_name
x509_extensions = v3_req
prompt = no
[req_distinguished_name]
CN = $cluster
[v3_req]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = serverAuth
subjectAltName = @alt_names
[alt_names]
DNS.1 = $cluster
DNS.2 = $cluster.$ns.svc
DNS.3 = $cluster.$ns.svc.$DOMAIN
EOF
}

function write_meb_req() {
  out_dir=$1

  cat > "$out_dir/meb-req.conf" << EOF
[req]
distinguished_name = req_distinguished_name
x509_extensions = v3_req
prompt = no
[req_distinguished_name]
CN = $MEB_RDN_CN
[v3_req]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = clientAuth
EOF
}

function make_ca() {
  out_dir=$1

  make_key "$out_dir/ca-key.pem"
  openssl req -new -x509 -sha384 -days "$VALIDITY_DAYS" \
          -key "$out_dir/ca-key.pem" \
          -out "$out_dir/ca.pem" \
          -config "$out_dir/ca-req.conf" \
          -extensions v3_ca
}

function make_cert() {
  out_dir=$1
  req_conf=$2
  key=$3
  cert=$4
  serial=$5

  make_key "$key"
  openssl req -new -sha384 -key "$key" \
          -out "$out_dir/tmp-$serial.csr" \
          -config "$req_conf" \
          -extensions v3_req
  openssl x509 -req -sha384 \
          -in "$out_dir/tmp-$serial.csr" \
          -days "$VALIDITY_DAYS" \
          -CA "$out_dir/ca.pem" \
          -CAkey "$out_dir/ca-key.pem" \
          -set_serial "$serial" \
          -out "$cert" \
          -extensions v3_req \
          -extfile "$req_conf"
  rm -f "$out_dir/tmp-$serial.csr"
}

function verify_cluster() {
  out_dir=$1
  ns=$2
  cluster=$3

  server_host="$cluster-1.$cluster-instances.$ns.svc.$DOMAIN"
  router_host="$cluster.$ns.svc.$DOMAIN"

  openssl verify -x509_strict -purpose sslserver \
          -verify_hostname "$server_host" \
          -CAfile "$out_dir/ca.pem" "$out_dir/server/tls.crt" >/dev/null
  openssl verify -x509_strict -purpose sslserver \
          -verify_hostname "$router_host" \
          -CAfile "$out_dir/ca.pem" "$out_dir/router/tls.crt" >/dev/null
  openssl verify -x509_strict -purpose sslclient \
          -CAfile "$out_dir/ca.pem" "$out_dir/meb/tls.crt" >/dev/null
}

function make_cluster() {
  ns=$1
  cluster=$2
  out_dir="$OUT_ROOT/$ns/$cluster"

  echo "Creating MEB TLS fixtures for $ns/$cluster"
  mkdir -p "$out_dir/server" "$out_dir/router" "$out_dir/meb"

  write_ca_req "$out_dir"
  write_server_req "$out_dir" "$ns" "$cluster"
  write_router_req "$out_dir" "$ns" "$cluster"
  write_meb_req "$out_dir"
  make_ca "$out_dir"
  make_cert "$out_dir" "$out_dir/server-req.conf" "$out_dir/server/tls.key" "$out_dir/server/tls.crt" 01
  make_cert "$out_dir" "$out_dir/router-req.conf" "$out_dir/router/tls.key" "$out_dir/router/tls.crt" 02
  make_cert "$out_dir" "$out_dir/meb-req.conf" "$out_dir/meb/tls.key" "$out_dir/meb/tls.crt" 03
  verify_cluster "$out_dir" "$ns" "$cluster"
}

function make_cluster_variant() {
  ns=$1
  cluster=$2
  variant=$3
  out_dir="$OUT_ROOT/$ns/$cluster/$variant"

  echo "Creating MEB TLS fixtures for $ns/$cluster/$variant"
  mkdir -p "$out_dir/server" "$out_dir/router" "$out_dir/meb"

  write_ca_req "$out_dir"
  write_server_req "$out_dir" "$ns" "$cluster"
  write_router_req "$out_dir" "$ns" "$cluster"
  write_meb_req "$out_dir"
  make_ca "$out_dir"
  make_cert "$out_dir" "$out_dir/server-req.conf" "$out_dir/server/tls.key" "$out_dir/server/tls.crt" 01
  make_cert "$out_dir" "$out_dir/router-req.conf" "$out_dir/router/tls.key" "$out_dir/router/tls.crt" 02
  make_cert "$out_dir" "$out_dir/meb-req.conf" "$out_dir/meb/tls.key" "$out_dir/meb/tls.crt" 03
  verify_cluster "$out_dir" "$ns" "$cluster"
}

rm -rf "$OUT_ROOT"
mkdir -p "$OUT_ROOT"

make_cluster "mebfull-backup-external-tlshelm" "meb-fb-ext-helm"
make_cluster "mebfull-backup-external-tlsraw" "meb-fb-ext-raw"
make_cluster "mebfull-restore-external-tlshelm" "meb-fr-ext-helm"
make_cluster "mebfull-restore-external-tlshelm" "meb-fr-ext-helm-restore"
make_cluster "mebfull-restore-external-tlsraw" "meb-fr-ext-raw"
make_cluster "mebfull-restore-external-tlsraw" "meb-fr-ext-raw-restore"
make_cluster "mebincremental-restore-external-tlshelm" "meb-inc-ext-helm"
make_cluster "mebincremental-restore-external-tlshelm" "meb-inc-ext-helm-restore"
make_cluster "mebincremental-restore-external-tlsraw" "meb-inc-ext-raw"
make_cluster "mebincremental-restore-external-tlsraw" "meb-inc-ext-raw-restore"
make_cluster "mebpitrrestore-external-tlshelm" "meb-pitr-ext-helm"
make_cluster "mebpitrrestore-external-tlshelm" "meb-pitr-ext-helm-restore"
make_cluster "mebpitrrestore-external-tlsraw" "meb-pitr-ext-raw"
make_cluster "mebpitrrestore-external-tlsraw" "meb-pitr-ext-raw-restore"
make_cluster_variant "mebtlsrotation-external-tlsraw" "meb-tls-rot-raw" "initial"
make_cluster_variant "mebtlsrotation-external-tlsraw" "meb-tls-rot-raw" "rotate1"
make_cluster_variant "mebtlsrotation-external-tlsraw" "meb-tls-rot-raw" "rotate2"

echo "MEB TLS fixtures written to $OUT_ROOT"
