Enterprise Backup Logic
=======================

The code in this file is not meant to be used from
other parts of operator code but is isolated. When
MEB features are being used it is put in a ConfigMap
and mounted into a **Sever** container. In consequence
it may only use Python dependency available in a 
server container. Also it must use a single flat 
directory without further subdirectories.
