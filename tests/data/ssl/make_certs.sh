
function ensure_rsa_key() {
  key_path=$1

  if [ ! -f $key_path ]; then
    openssl genrsa 2048 > $key_path
  fi
}

function make_ca() {
  ca_prefix=$1
  cn=$2
  days=$3

  ensure_rsa_key $ca_prefix-key.pem

  openssl req -new -x509 -nodes -days $days -key $ca_prefix-key.pem -subj /CN=$cn -out $ca_prefix.pem \
          -addext "subjectKeyIdentifier = hash" \
          -addext "authorityKeyIdentifier = keyid:always,issuer" \
          -addext "basicConstraints = critical, CA:true" \
          -addext "keyUsage = critical, keyCertSign, cRLSign"
}

function make_cert() {
  ca_prefix=$1
  req_conf=$2
  out_prefix=$3
  serial=$4
  days=$5

  echo "Generating $out_prefix"

  ensure_rsa_key out/$out_prefix-key.pem

  openssl req -new \
          -key out/$out_prefix-key.pem -out out/$out_prefix-req.pem\
          -config $req_conf

  openssl x509 -req -in out/$out_prefix-req.pem -days $days \
         -CA $ca_prefix.pem -CAkey $ca_prefix-key.pem -set_serial $serial -out out/$out_prefix-cert.pem\
         -extensions 'v3_req' -extfile $req_conf
}

mkdir -p out

# Create CA1
echo "Creating CA1"
make_ca out/ca "Test_CA1" 36500

# Create Server Cert
echo
echo "Creating Server Cert"

make_cert out/ca server-req.conf server 01 3650
make_cert out/ca server-req.conf server2 02 7300

# Create Router Cert
echo
echo "Creating Router Cert"
make_cert out/ca router-req.conf router 01 3650
make_cert out/ca router-req.conf router2 02 7300

# Create CA-B
echo "Creating CA-B"
make_ca out/cab "Test_CA-B" 73000

# Create Server Cert
echo
echo "Creating Server Cert"
make_cert out/cab server-req.conf serverb 01 10920

# Create Router Cert
echo
echo "Creating Router Cert"
make_cert out/cab router-req.conf routerb 01 10920

# Create Another Server Cert
echo
echo "Creating Server Cert (to be revoked in crl)"
make_cert out/cab server-req.conf serverb-rev 02 10920

cat out/serverb-rev-cert.pem  > out/crl.pem

# delete unneeded files
rm out/*req.pem
