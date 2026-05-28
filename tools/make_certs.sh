#!/bin/bash
#
# Copyright (c) 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/

set -euo pipefail
umask 077

DOMAIN_DEFAULT="cluster.local"
VALIDITY_DAYS_DEFAULT=3650
CA_NAME_DEFAULT="MySQL_Operator_CA"
KEY_BITS=3072

function usage() {
  echo "Usage:"
  echo "  $0 --interactive"
  echo "  $0 OUT_DIR NAMESPACE CLUSTER_NAME [DOMAIN] [VALIDITY_DAYS] [CA_CN]"
}

function prompt_default() {
  prompt=$1
  default=$2
  value=""

  read -r -p "$prompt [$default]: " value
  if [[ -z "$value" ]]; then
    value=$default
  fi
  printf "%s" "$value"
}

function validate_dns_label() {
  name=$1
  value=$2

  if [[ ! "$value" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]]; then
    echo "$name must be a Kubernetes DNS label: $value" >&2
    exit 1
  fi
}

function validate_domain() {
  value=$1

  if [[ ! "$value" =~ ^[A-Za-z0-9.-]+$ ]]; then
    echo "DOMAIN contains invalid characters: $value" >&2
    exit 1
  fi
}

function validate_days() {
  value=$1

  if [[ ! "$value" =~ ^[0-9]+$ || "$value" -lt 1 ]]; then
    echo "VALIDITY_DAYS must be a positive integer: $value" >&2
    exit 1
  fi
}

function validate_subject_value() {
  name=$1
  value=$2

  if [[ -z "$value" || "$value" =~ [/\[\]=] ]]; then
    echo "$name contains invalid characters: $value" >&2
    exit 1
  fi
}

function validate_out_dir() {
  value=$1

  if [[ -z "$value" || "$value" == "/" ]]; then
    echo "OUT_DIR must not be empty or /" >&2
    exit 1
  fi
}

function make_key() {
  key=$1

  openssl genpkey -algorithm RSA \
          -pkeyopt rsa_keygen_bits:$KEY_BITS \
          -pkeyopt rsa_keygen_pubexp:65537 \
          -out "$key"
}

function write_ca_req() {
  out_dir=$1
  ca_name=$2

  cat > "$out_dir/ca-req.conf" << EOF
[req]
distinguished_name = req_distinguished_name
x509_extensions = v3_ca
prompt = no
[req_distinguished_name]
CN = $ca_name
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
  domain=$4

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
DNS.1 = *.$cluster-instances.$ns.svc.$domain
DNS.2 = *.$cluster-instances
EOF
}

function write_router_req() {
  out_dir=$1
  ns=$2
  cluster=$3
  domain=$4

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
DNS.3 = $cluster.$ns.svc.$domain
EOF
}

function make_ca() {
  out_dir=$1
  days=$2

  make_key "$out_dir/ca-key.pem"
  openssl req -new -x509 -sha384 -days "$days" \
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
  days=$6

  make_key "$key"
  openssl req -new -sha384 -key "$key" \
          -out "$out_dir/tmp-$serial.csr" \
          -config "$req_conf" \
          -extensions v3_req
  openssl x509 -req -sha384 \
          -in "$out_dir/tmp-$serial.csr" \
          -days "$days" \
          -CA "$out_dir/ca.pem" \
          -CAkey "$out_dir/ca-key.pem" \
          -set_serial "$serial" \
          -out "$cert" \
          -extensions v3_req \
          -extfile "$req_conf"
  rm -f "$out_dir/tmp-$serial.csr"
}

function verify_output() {
  out_dir=$1
  ns=$2
  cluster=$3
  domain=$4

  openssl verify -x509_strict -purpose sslserver \
          -verify_hostname "$cluster-1.$cluster-instances.$ns.svc.$domain" \
          -CAfile "$out_dir/ca.pem" "$out_dir/server/tls.crt" >/dev/null
  openssl verify -x509_strict -purpose sslserver \
          -verify_hostname "$cluster.$ns.svc.$domain" \
          -CAfile "$out_dir/ca.pem" "$out_dir/router/tls.crt" >/dev/null
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

if [[ "${1:-}" == "--interactive" ]]; then
  if [[ $# -ne 1 ]]; then
    usage >&2
    exit 1
  fi
  cluster=$(prompt_default "Cluster name" "mycluster")
  ns=$(prompt_default "Namespace" "default")
  out_dir=$(prompt_default "Output directory" "./out/$cluster")
  domain=$(prompt_default "Kubernetes cluster domain" "$DOMAIN_DEFAULT")
  days=$(prompt_default "Certificate validity days" "$VALIDITY_DAYS_DEFAULT")
  ca_name=$(prompt_default "CA common name" "$CA_NAME_DEFAULT")
else
  if [[ $# -lt 3 || $# -gt 6 ]]; then
    usage >&2
    exit 1
  fi
  out_dir=$1
  ns=$2
  cluster=$3
  domain=${4:-$DOMAIN_DEFAULT}
  days=${5:-$VALIDITY_DAYS_DEFAULT}
  ca_name=${6:-$CA_NAME_DEFAULT}
fi

validate_dns_label "NAMESPACE" "$ns"
validate_dns_label "CLUSTER_NAME" "$cluster"
validate_domain "$domain"
validate_days "$days"
validate_subject_value "CA_CN" "$ca_name"
validate_out_dir "$out_dir"

mkdir -p "$out_dir/server" "$out_dir/router"
write_ca_req "$out_dir" "$ca_name"
write_server_req "$out_dir" "$ns" "$cluster" "$domain"
write_router_req "$out_dir" "$ns" "$cluster" "$domain"
make_ca "$out_dir" "$days"
make_cert "$out_dir" "$out_dir/server-req.conf" "$out_dir/server/tls.key" "$out_dir/server/tls.crt" 01 "$days"
make_cert "$out_dir" "$out_dir/router-req.conf" "$out_dir/router/tls.key" "$out_dir/router/tls.crt" 02 "$days"
verify_output "$out_dir" "$ns" "$cluster" "$domain"

echo "Certificates written to $out_dir"
echo "CA: $out_dir/ca.pem"
echo "Server TLS secret files: $out_dir/server/tls.crt $out_dir/server/tls.key"
echo "Router TLS secret files: $out_dir/router/tls.crt $out_dir/router/tls.key"
